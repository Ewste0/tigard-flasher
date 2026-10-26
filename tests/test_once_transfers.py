"""SPC560 register batching: identical pin clocks, fewer USB submissions."""
import unittest
from unittest.mock import patch

from spc5tool.chips import SPC560B64
from spc5tool.errors import DriverError
from spc5tool.memory import OnceMemory
from spc5tool.mpsse import MpsseError, MpsseTransport
from spc5tool.once import OnCE
from spc5tool.tap import JtagTap
from tests.fake_mpsse import FakeMpsse
from tests.model_once import OnceTarget
from tests.simulator import SimMemory
from spc5tool.flash import Flash
from spc5tool.geometry import select_blocks


class TracedTarget(OnceTarget):
    def __init__(self, delay=0, error=False):
        self.trace = []
        self.delay, self.remaining, self.error = delay, 0, error
        super().__init__()

    def clock(self, tms, tdi):
        self.trace.append((tms, tdi))
        if self.remaining:
            self.remaining -= 1
            if not self.remaining:
                self.halted = True
        return super().clock(tms, tdi)

    def execute(self):
        super().execute()
        self.instruction_error = self.error
        if self.delay:
            self.remaining = self.delay
            self.halted = False

    def _enter(self, nxt):
        if nxt == 'DRCAPTURE' and self.aux and self.command == 0x210:
            if not self.halted:
                raise AssertionError('CPUSCR read before checked DEBUG')
        super()._enter(nxt)


class CountedMpsse(FakeMpsse):
    def __init__(self, **kwargs):
        super().__init__(0)
        self.tgt = TracedTarget(**kwargs)
        self.writes = self.reads = 0
        self.fail_write = self.fail_read = False

    def write_data(self, data):
        self.writes += 1
        if self.fail_write:
            return 0
        return super().write_data(data)

    def read_data_bytes(self, size):
        self.reads += 1
        return b'' if self.fail_read else super().read_data_bytes(size)


class SeparateTransfers(OnCE):
    """The pre-0.7.8 packaging, retaining the same scans and status checks."""
    def _exchange(self, scans, *, read=False):
        return scans(self.tap)


class CaptureEveryResult(OnCE):
    """0.7.8 behavior: retrieve CPUSCR even when the caller discards WBBR."""
    def step(self, instruction, operand=0, **kwargs):
        return super().step(instruction, operand, **{**kwargs, 'capture_result': True})


class OnceTransferTests(unittest.TestCase):
    def setup_memory(self, cls=OnCE, **kwargs):
        io = CountedMpsse(**kwargs)
        tap = JtagTap(MpsseTransport(io))
        once = cls(tap, SPC560B64)
        once.attach()
        io.writes = io.reads = 0
        io.tgt.trace.clear()
        return io, OnceMemory(once)

    def test_identical_pin_stream_data_and_status_checks(self):
        outputs = []
        for cls in (SeparateTransfers, OnCE):
            io, memory = self.setup_memory(cls)
            io.tgt.write_word(0x800000, 0x12345678)
            self.assertEqual(memory.read32(0x800000), 0x12345678)
            counts = (io.writes, io.reads)
            memory.write32(0x800004, 0xA5C39687)
            self.assertEqual(memory.read32(0x800004), 0xA5C39687)
            outputs.append((list(io.tgt.trace), io.tgt.executed, counts))
        self.assertEqual(outputs[0][:2], outputs[1][:2])
        self.assertEqual(outputs[0][2], (15, 2))
        self.assertEqual(outputs[1][2], (3, 2))

    def test_busy_status_is_polled_before_result_without_replay(self):
        io, memory = self.setup_memory(delay=100)
        io.tgt.write_word(0x800000, 0x12345678)
        self.assertEqual(memory.read32(0x800000), 0x12345678)
        self.assertGreater(io.reads, 2)
        self.assertEqual(io.tgt.executed, [0x501F0000])

    def test_instruction_error_never_submits_next_store(self):
        io, memory = self.setup_memory(error=True)
        with self.assertRaisesRegex(DriverError, 'OnCE instruction execution error'):
            memory.write32(0xC3F88000, 0x11)
        self.assertEqual(io.tgt.executed, [0x1BE1D000])
        self.assertNotIn(0xC3F88000, io.tgt.memory)

    def test_transport_failure_never_replays_instruction(self):
        for failure in ('fail_write', 'fail_read'):
            with self.subTest(failure=failure):
                io, memory = self.setup_memory()
                io.tgt.write_word(0x800000, 0x12345678)
                setattr(io, failure, True)
                with self.assertRaises(MpsseError):
                    memory.read32(0x800000)
                self.assertLessEqual(len(io.tgt.executed), 1)
                self.assertEqual(io.writes, 1 if failure == 'fail_write' else 2)

    def test_diagnostic_usb_failure_retains_transport_error_and_stops_io(self):
        io, memory = self.setup_memory(error=True)
        read = io.read_data_bytes
        def fail_after_status(size):
            return b'' if io.reads else read(size)
        with patch.object(io, 'read_data_bytes', fail_after_status):
            with self.assertRaises(MpsseError):
                memory.write32(0xC3F88000, 0x11)
        self.assertEqual(io.tgt.executed, [0x1BE1D000])
        self.assertEqual(io.writes, 3)  # GO, OSR, failed CPUSCR; no more diagnostics.

    def test_write_discards_only_unused_results_and_read_still_captures(self):
        for cls, counts in ((CaptureEveryResult, (6, 4)), (OnCE, (4, 2))):
            with self.subTest(cls=cls.__name__):
                io, memory = self.setup_memory(cls)
                memory.write32(0x800000, 0x12345678)
                self.assertEqual((io.writes, io.reads), counts)
                self.assertEqual(io.tgt.executed, [0x1BE1D000, 0x543F0000])
                self.assertEqual(memory.read32(0x800000), 0x12345678)
                self.assertEqual(io.reads, counts[1] + 2)

    def test_write_still_polls_pending_instruction_before_next_go(self):
        io, memory = self.setup_memory(delay=100)
        memory.write32(0x800000, 0xA5C39687)
        self.assertGreater(io.reads, 2)
        self.assertEqual(io.tgt.executed, [0x1BE1D000, 0x543F0000])
        self.assertEqual(memory.read32(0x800000), 0xA5C39687)

    def test_selftest_keeps_checked_result_capture(self):
        io, memory = self.setup_memory()
        memory.initialize()
        self.assertEqual((io.writes, io.reads), (6, 4))
        self.assertEqual(io.tgt.executed, [0x1BE1D001, 0x1BE1D001])

    def test_each_write_step_usb_failure_prevents_subsequent_instruction(self):
        for fail_status in (1, 2):
            with self.subTest(fail_status=fail_status):
                io, memory = self.setup_memory()
                read = io.read_data_bytes
                def fail(size):
                    return b'' if io.reads == fail_status - 1 else read(size)
                with patch.object(io, 'read_data_bytes', fail):
                    with self.assertRaises(MpsseError):
                        memory.write32(0x800000, 0x12345678)
                self.assertEqual(len(io.tgt.executed), fail_status)
                self.assertEqual(io.writes, 2 * fail_status)

    def test_full_c90_program_cycle_preserves_flash_commands_and_cuts_responses(self):
        traffic = []
        for cls in (CaptureEveryResult, OnCE):
            measurements = []
            for size in (8, 16):
                io, memory = self.setup_memory(cls)
                controller = SimMemory(SPC560B64)
                io.tgt.read_word = controller.read32
                io.tgt.write_word = controller.write32
                flash = Flash(memory, SPC560B64)
                self.assertEqual(flash.read(0, size), b'\xff' * size)
                io.writes = io.reads = 0
                # Internal helper's same-session blank proof is supplied above.
                flash._program_erased(0, b'\xA5' * size, select_blocks(SPC560B64, 0, size))
                measurements.append((io.writes, io.reads, list(controller.log)))
                flash.verify(0, b'\xA5' * size)
                self.assertFalse(controller.controllers[0xC3F88000]['mcr'] & 0x15)
                self.assertEqual(controller.controllers[0xC3F88000]['regs'][4], 0xFFFFFFFF)
            traffic.append(measurements)
        for index in (0, 1):
            self.assertEqual(traffic[0][index][2], traffic[1][index][2])
        per_unit = [(m[1][0] - m[0][0], m[1][1] - m[0][1]) for m in traffic]
        self.assertEqual(per_unit, [(51, 34), (37, 20)])

    def test_speed_stream_halves_per_unit_response_roundtrips(self):
        counts = []
        for size in (8, 16):
            io, memory = self.setup_memory()
            controller = SimMemory(SPC560B64)
            io.tgt.read_word = controller.read32
            io.tgt.write_word = controller.write32
            image = b'\xA5' * size + b'\xff' * (0x8000 - size)
            Flash(memory, SPC560B64).write_speed(0, image)
            counts.append((io.writes, io.reads))
            self.assertEqual(controller.regions[0][:0x8000], image)
            self.assertFalse(controller.controllers[0xC3F88000]['mcr'] & 0x15)
        self.assertEqual(tuple(b - a for a, b in zip(*counts)), (19, 10))
