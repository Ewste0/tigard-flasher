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
