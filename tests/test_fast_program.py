"""Independent wire and controller checks for MPC5748 CFLASH quad-pages."""
import unittest
from unittest.mock import Mock, patch

from spc5tool.chips import SPC5748G
from spc5tool.errors import NexusBusError, OperationTimeout, VerificationError
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

    def test_controller_write_one_exchange_and_no_replay_on_delayed_reply(self):
        for latency in (8, 1000):
            memory, io = self.memory(latency=latency)
            io.writes = 0
            memory.write32(0xFFFE0000, 0x11)
            self.assertEqual(io.tgt.writes, [(0xFFFE0000, b'\x00\x00\x00\x11')])
            if latency == 8:
                self.assertEqual((io.writes, io.reads), (1, 1))
            else:
                self.assertGreater(io.reads, 1)

    def test_controller_bus_error_no_replay_or_subsequent_write(self):
        memory, io = self.memory(error_address=0xFFFE0000)
        with self.assertRaises(NexusBusError):
            memory.write32(0xFFFE0000, 0x10)
        self.assertEqual(len(io.tgt.writes), 1)
        self.assertEqual(io.reads, 1)

    def test_controller_queue_excludes_other_regions_and_standard_mode(self):
        memory, io = self.memory()
        for address in (0xFA8000, 0x400100, 0xFFFE0004, 0xFFFE0200):
            self.assertFalse(memory._queued_controller(address))
        memory.fast_read = False
        self.assertFalse(memory._queued_controller(0xFFFE0000))

    def test_controller_usb_failure_prevents_cleanup_bus_writes(self):
        memory = SimMemory(SPC5748G)
        flash = Flash(memory, SPC5748G)
        flash.bulk_program = True
        original = memory.write32
        def broken(address, value):
            if address == 0xFFFE0000 and value == 0x11:
                raise MpsseError('short USB reply after EHV request')
            original(address, value)
        memory.write32 = Mock(side_effect=broken)
        with self.assertRaises(MpsseError):
            flash.program(0xFA8000, bytes(range(128)))
        self.assertTrue(flash.poisoned)
        self.assertEqual(memory.write32.call_args.args, (0xFFFE0000, 0x11))


class FastPatchTests(unittest.TestCase):
    def flash(self):
        memory = SimMemory(SPC5748G)
        flash = Flash(memory, SPC5748G)
        flash.bulk_program = True
        return flash, memory

    def test_three_passes_back_up_before_changes_and_preserve_whole_blocks(self):
        flash, memory = self.flash()
        address, size = 0xFA8000, 0x8000
        original = bytes((i % 250) + 1 for i in range(size))
        memory.regions[0xF90000][address - 0xF90000:address - 0xF90000 + size] = original
        backup, progress = [], []
        flash.progress = lambda _, d, t: progress.append(d / t)
        def save(blocks):
            self.assertFalse(any(a == 0xFFFE0000 and v & 1 for a, v in memory.log))
            backup.extend(blocks)
        with patch.object(memory, 'read', wraps=memory.read) as reads:
            # Unaligned image crosses two blocks. Whole rebuilt blocks must be
            # verified, including bytes outside this three-byte image.
            flash.patch(address + 0x3FFF, b'XYZ', save)
        self.assertEqual(b''.join(d for _, d in backup), original)
        expected = original[:0x3FFF] + b'XYZ' + original[0x4002:]
        self.assertEqual(memory.read(address, size), expected)
        self.assertEqual(sum(c.args[1] for c in reads.call_args_list), 3 * size)
        self.assertEqual(flash.write_stats['changed_blocks'], 2)
        self.assertEqual(progress, sorted(progress))
        self.assertEqual(progress[-1], 1)
        self.assertEqual(memory.controllers[0xFFFE0000]['regs'][0x10], 0xFFFFFFFF)

    def test_unchanged_block_is_backed_up_and_verified_without_pulses(self):
        flash, memory = self.flash()
        backup = []
        with patch.object(memory, 'read', wraps=memory.read) as reads:
            flash.patch(0xFA8001, b'\xff', backup.extend)
        self.assertEqual(len(backup), 1)
        self.assertEqual(sum(c.args[1] for c in reads.call_args_list), 2 * 0x4000)
        self.assertFalse(any(a == 0xFFFE0000 and v & 1 for a, v in memory.log))
        self.assertEqual(flash.write_stats['unchanged_blocks'], 1)

    def test_backup_failure_no_flash_or_lock_change(self):
        flash, memory = self.flash()
        with self.assertRaises(OSError):
            flash.patch(0xFA8000, b'abcdefgh', Mock(side_effect=OSError('disk full')))
        self.assertFalse(any(a != 0xFFFE0000 or v != 0xC000 for a, v in memory.log))

    def test_failed_blank_check_never_reaches_program(self):
        flash, memory = self.flash()
        memory.regions[0xF90000][0x18000:0x18008] = b'old data'
        execute = memory._execute
        def no_erase(base, controller):
            if controller['mcr'] & 4:
                controller['mcr'] |= 0x600  # False positive PEG; data unchanged.
            else:
                execute(base, controller)
        memory._execute = no_erase
        with self.assertRaises(VerificationError):
            flash.patch(0xFA8000, b'new data', lambda _: None)
        self.assertFalse(any(a == 0xFFFE0000 and v == 0x11 for a, v in memory.log))

    def test_corruption_outside_image_fails_final_verification(self):
        flash, memory = self.flash()
        program = flash._program_erased
        def corrupt(*args):
            program(*args)
            memory.regions[0xF90000][0x18000 + 0x1000] = 0
        flash._program_erased = corrupt
        with self.assertRaisesRegex(VerificationError, '00fa9000'):
            flash.patch(0xFA8000, b'new data', lambda _: None)

    def test_blank_backup_does_not_skip_ecc_reinitializing_erase(self):
        flash, memory = self.flash()
        flash.patch(0xFA8000, bytes(range(128)), lambda _: None)
        self.assertEqual(sum(a == 0xFFFE0000 and v == 5 for a, v in memory.log), 1)
        self.assertEqual(sum(a == 0xFFFE0000 and v == 0x11 for a, v in memory.log), 1)


if __name__ == '__main__':
    unittest.main()
