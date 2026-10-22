"""Independent wire and controller checks for MPC5748 CFLASH quad-pages."""
import unittest
from unittest.mock import Mock

from spc5tool.chips import SPC5748G
from spc5tool.errors import NexusBusError, OperationTimeout
from spc5tool.flash import Flash
from spc5tool.memory import NexusMemory
from spc5tool.mpsse import MpsseError, MpsseTransport
from spc5tool.once import OnCE
from spc5tool.tap import JtagTap
from tests.fake_mpsse import FakeTarget
from tests.simulator import SimMemory
from tests.test_fast_read import CountingMpsse


class BufferTarget(FakeTarget):
    def __init__(self, latency=8, error_address=None):
        super().__init__(0x0988101D, 10)
        self.instruction = self.register = self.address = self.ticks = self.ready_at = 0
        self.address_phase = True
        self.write = False
        self.latency, self.error_address = latency, error_address
        self.writes = []

    def clock(self, tms, tdi):
        self.ticks += 1
        return super().clock(tms, tdi)

    def _enter(self, nxt):
        if nxt == 'IRCAPTURE':
            self.ir_shift = 0x209
        elif nxt == 'IRUPDATE':
            self.instruction = self.ir_shift
            self.address_phase = True
        elif nxt == 'DRCAPTURE':
            if self.instruction != 0x7C:
                self.dr, self.dr_width = self.idcode, 32
            elif self.address_phase:
                self.dr, self.dr_width = 0, 8
            else:
                self.dr, self.dr_width = 0, 32
                if not self.write:
                    assert self.register == 7, 'Only RWCS may be read'
                    self.dr = 0x50000004 | (2 if self.address == self.error_address else
                                           int(self.ticks < self.ready_at))
        elif nxt == 'DRUPDATE' and self.instruction == 0x7C:
            if self.address_phase:
                self.register, self.write = self.dr >> 1, bool(self.dr & 1)
            elif self.write:
                if self.register == 9:
                    self.address = self.dr
                elif self.register == 7:
                    assert self.dr & 0xFF3FFFFF == 0xD0000004, 'Must be a single word write'
                elif self.register == 10:
                    self.writes.append((self.address, self.dr.to_bytes(4, 'little')))
                    self.ready_at = self.ticks + self.latency
                else:
                    raise AssertionError('Unexpected Nexus write')
            self.address_phase = not self.address_phase
        self.state = nxt


class FastProgramTests(unittest.TestCase):
    def memory(self, **options):
        io = CountingMpsse()
        io.tgt = BufferTarget(**options)
        tap = JtagTap(MpsseTransport(io))
        tap.reset()
        memory = NexusMemory(OnCE(tap, SPC5748G), fast_read=True, read_idle_clocks=50)
        io.reads = 0
        return memory, io

    def test_buffer_one_reply_exact_addresses_and_byte_order(self):
        memory, io = self.memory()
        data = bytes(range(128))
        memory.write_program_buffer(0xFA8000, data)
        self.assertEqual(io.reads, 1)
        self.assertEqual(io.tgt.writes, [(0xFA8000 + n, data[n:n + 4]) for n in range(0, 128, 4)])
        self.assertEqual(memory.once.tap.state, 'IDLE')

    def test_error_and_not_ready_never_retry_buffer(self):
        for options, error in (({'error_address': 0xFA8040}, NexusBusError), ({'latency': 1000}, OperationTimeout)):
            with self.subTest(options=options):
                memory, io = self.memory(**options)
                with self.assertRaises(error):
                    memory.write_program_buffer(0xFA8000, bytes(range(128)))
                self.assertEqual(io.reads, 1)
                self.assertEqual(len(io.tgt.writes), 32)

    def test_buffer_rejects_otp_partial_and_unaligned_without_io(self):
        for address, size in ((0x400100, 128), (0xFA8008, 128), (0xFA8000, 64), (0x1580000, 128)):
            memory, io = self.memory()
            with self.assertRaises(ValueError):
                memory.write_program_buffer(address, bytes(size))
            self.assertEqual(io.reads, 0)
            self.assertEqual(io.tgt.writes, [])

    def test_quad_pages_reduce_pulses_and_preserve_edges(self):
        for address, size in ((0xFA8000, 256), (0xFA8008, 256), (0xFABFF8, 272)):
            with self.subTest(address=address, size=size):
                memory = SimMemory(SPC5748G)
                flash = Flash(memory, SPC5748G)
                flash.bulk_program = True
                data = bytes((i % 250) + 1 for i in range(size))
                flash.program(address, data)
                self.assertEqual(flash.read(address, size), data)
                self.assertEqual(flash.read(address - 8, 8), b'\xff' * 8)
                self.assertEqual(flash.read(address + size, 8), b'\xff' * 8)
                pulses = sum(a == 0xFFFE0000 and v == 0x11 for a, v in memory.log)
                self.assertLess(pulses, size // 8)
                if address == 0xFA8000:
                    self.assertEqual(pulses, 2)

    def test_otp_and_ff_units_keep_single_unit_path(self):
        for address, data in ((0x400100, bytes(range(128))),
                              (0xFA8000, b'\xff' * 8 + bytes(range(120)))):
            memory = SimMemory(SPC5748G)
            flash = Flash(memory, SPC5748G)
            flash.bulk_program = True
            flash.program(address, data)
            self.assertEqual(flash.read(address, len(data)), data)
            pulses = sum(a == 0xFFFE0000 and v == 0x11 for a, v in memory.log)
            self.assertEqual(pulses, 16 if address == 0x400100 else 15)

    def test_buffer_failure_never_asserts_ehv(self):
        for error in (OperationTimeout('not ready'), MpsseError('short USB read')):
            with self.subTest(error=type(error).__name__):
                memory = SimMemory(SPC5748G)
                memory.write_program_buffer = Mock(side_effect=error)
                memory.fast_read = True
                flash = Flash(memory, SPC5748G)
                flash.bulk_program = True
                with self.assertRaises(type(error)):
                    flash.program(0xFA8000, bytes(range(128)))
                self.assertFalse(any(a == 0xFFFE0000 and v & 1 for a, v in memory.log))
                self.assertEqual(memory.read(0xFA8000, 128), b'\xff' * 128)
                self.assertEqual(flash.poisoned, isinstance(error, MpsseError))


if __name__ == '__main__':
    unittest.main()
