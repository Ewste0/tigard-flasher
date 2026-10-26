"""Offline Speed contracts; no USB or real target is opened.

The C90 interlock sequence follows ST AN4194 2.1 steps 7–9. These tests
validate sequencing and traffic, not elapsed time or silicon compatibility.
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spc5tool.chips import SPC560B64, CHIPS
from spc5tool.cli import main
from spc5tool.errors import DriverError, OperationTimeout
from spc5tool.flash import Flash, PGM, EHV, PEG, DONE
from spc5tool.mpsse import MpsseError
from tests.test_batch_erase import RecordingMemory, lock_values


class NoArrayReads(RecordingMemory):
    def read(self, address, size):
        raise AssertionError("Speed must not read the flash array")


class StopRequested(Exception):
    pass


class SpeedTests(unittest.TestCase):
    def setup_flash(self):
        memory = NoArrayReads(SPC560B64)
        for region in memory.regions.values():
            region[:] = b'\xA5' * len(region)
        return memory, Flash(memory, SPC560B64)

    def assert_idle(self, memory, locks):
        self.assertEqual(lock_values(memory), locks)
        self.assertTrue(all(not c['mcr'] & 0x15 for c in memory.controllers.values()))

    def test_whole_blocks_stream_across_gaps_and_boundaries_without_reads(self):
        for address, size, boundary in ((0, 0xC000, 0x8000), (0x800000, 0x8000, 0x4000)):
            with self.subTest(address=address):
                memory, flash = self.setup_flash()
                before = {a: bytes(d) for a, d in memory.regions.items()}
                locks = lock_values(memory)
                image = bytearray(b'\xff' * size)
                offsets = (0, boundary - 8, boundary, size - 8)
                for n in offsets:
                    image[n:n + 8] = b'\x12\x34\x56\x78\xff\xff\xff\xff'
                events = []
                flash.progress = lambda p, d, t: events.append((p, d / t))
                flash.write_speed(address, image)
                for base, original in before.items():
                    expected = bytearray(original)
                    if base <= address < base + len(original):
                        expected[address - base:address - base + size] = image
                    self.assertEqual(memory.regions[base], expected)
                self.assertEqual([p[1] for p in memory.pulses], [4, 16, 16, 16, 16])
                self.assertEqual([p[2] for p in memory.pulses[1:]], [address + n for n in offsets])
                base = memory.pulses[0][0]
                mcr = [v for a, v in memory.log if a == base]
                first = mcr.index(PGM)
                self.assertNotIn(0, mcr[first:-1])  # PGM held through every unit.
                self.assertEqual(flash.write_stats['program_bytes'], 32)
                self.assertEqual(flash.write_stats['skipped_ff_bytes'], size - 32)
                fractions = [f for _, f in events]
                self.assertEqual(fractions, sorted(fractions))
                self.assertEqual(fractions[-1], 1)
                self.assertFalse(any('verify' in p or 'blank check' in p for p, _ in events))
                self.assert_idle(memory, locks)

    def test_all_ff_still_erases_once_without_programming(self):
        memory, flash = self.setup_flash()
        flash.write_speed(0, b'\xff' * 0x180000)
        self.assertEqual([p[1] for p in memory.pulses], [4])
        self.assertEqual(memory.regions[0], b'\xff' * 0x180000)
        self.assertEqual(flash.write_stats['program_bytes'], 0)
        self.assertEqual(flash.write_stats['skipped_ff_bytes'], 0x180000)
        self.assertFalse(flash.write_stats['verified'])

    def test_erase_all_selected_blocks_one_pulse_no_blank_read(self):
        for address, size in ((0, 0x180000), (0x800000, 0x10000)):
            memory, flash = self.setup_flash()
            locks = lock_values(memory)
            flash.erase_speed(address, size)
            self.assertEqual(len(memory.pulses), 1)
            self.assertEqual(memory.regions[address], b'\xff' * size)
            self.assertEqual(flash.erase_stats['blank_check_seconds'], 0)
            self.assert_idle(memory, locks)

    def test_bad_ranges_and_families_rejected_before_io(self):
        cases = [(SPC560B64, a, n) for a, n in (
            (0, 8), (8, 0x8000), (0, 0), (0x200000, 0x4000),
            (0x400000, 0x2000), (0xC00000, 0x2000))]
        cases += [(chip, chip.regions[0].base, 0x8000)
                  for chip in CHIPS.values() if chip.key != 'spc560b64']
        for chip, address, size in cases:
            for write in (True, False):
                with self.subTest(chip=chip.key, address=address, write=write):
                    memory = NoArrayReads(chip)
                    flash = Flash(memory, chip)
                    with self.assertRaises(ValueError):
                        (flash.write_speed(address, b'\xff' * size) if write
                         else flash.erase_speed(address, size))
                    self.assertEqual(memory.accesses, [])

    def test_controller_failure_and_timeout_prevent_next_unit(self):
        for phase in (4, PGM):
            for fault in ('peg', 'error', 'timeout'):
                with self.subTest(phase=phase, fault=fault):
                    memory, flash = self.setup_flash()
                    locks = lock_values(memory)
                    execute = memory._execute
                    def fail(base, c):
                        execute(base, c)
                        if c['mcr'] & 0x14 == phase:
                            if fault == 'peg':
                                c['mcr'] &= ~PEG
                            elif fault == 'error':
                                c['mcr'] |= 0x8000
                            else:
                                c['mcr'] &= ~DONE
                    flash.timeout = 0.00001
                    with patch.object(memory, '_execute', fail):
                        with self.assertRaises(OperationTimeout if fault == 'timeout' else DriverError):
                            flash.write_speed(0, b'\x5A' * 16 + b'\xff' * (0x8000 - 16))
                    self.assertEqual(len(memory.pulses), 1 if phase == 4 else 2)
                    self.assert_idle(memory, locks)

    def test_stop_waits_for_pulse_cleanup_and_restores_locks(self):
        for phase in (4, PGM):
            memory, flash = self.setup_flash()
            locks = lock_values(memory)
            def stop():
                if any(p[1] == phase for p in memory.pulses):
                    self.assertTrue(all(not c['mcr'] & EHV for c in memory.controllers.values()))
                    raise StopRequested()
            flash.cancel_check = stop
            with self.assertRaises(StopRequested):
                flash.write_speed(0, b'\x5A' * 16 + b'\xff' * (0x8000 - 16))
            self.assertEqual(len(memory.pulses), 1 if phase == 4 else 2)
            self.assert_idle(memory, locks)

    def test_usb_loss_during_stream_stops_all_io(self):
        for fail_at in ('data', 'start', 'status', 'finish'):
            memory, flash = self.setup_flash()
            read, write = memory.read32, memory.write32
            lost = False
            def disconnect():
                nonlocal lost
                lost = True
                raise MpsseError('injected USB loss')
            def checked_write(a, v):
                self.assertFalse(lost, 'I/O after disconnect')
                c = memory.controllers[0xC3F88000]
                if ((fail_at == 'data' and a == 4 and c['mcr'] & PGM) or
                    (fail_at == 'start' and a == 0xC3F88000 and v == PGM | EHV) or
                    (fail_at == 'finish' and a == 0xC3F88000 and v == PGM and c['mcr'] & EHV)):
                    disconnect()
                return write(a, v)
            def checked_read(a):
                self.assertFalse(lost, 'I/O after disconnect')
                if fail_at == 'status' and a == 0xC3F88000 and memory.controllers[a]['mcr'] & 0x11 == 0x11:
                    disconnect()
                return read(a)
            with patch.object(memory, 'read32', checked_read), patch.object(memory, 'write32', checked_write):
                with self.assertRaises(MpsseError):
                    flash.write_speed(0, b'\x5A' * 16 + b'\xff' * (0x8000 - 16))
            self.assertTrue(flash.poisoned)

    def test_cli_speed_requires_idcode_but_no_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'image.bin'
            image.write_bytes(b'\x5A' * 8 + b'\xff' * (0x8000 - 8))
            for action in ('write', 'erase'):
                argv = [action, 'spc560b64', '--address', '0', '--speed']
                argv += [str(image)] if action == 'write' else ['--length', '0x8000']
                with patch('spc5tool.cli.backend') as backend, contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(main(argv), 0)
                    self.assertFalse(json.loads(out.getvalue())['executed'])
                    backend.assert_not_called()
                with patch('spc5tool.cli.backend') as backend, contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(argv + ['--execute']), 2)
                    backend.assert_not_called()
                memory, flash = self.setup_flash()
                with patch('spc5tool.cli.backend', return_value=contextlib.nullcontext(flash)), contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(main(argv + ['--execute', '--expect-idcode', '0x0988101D']), 0)
                    result = json.loads(out.getvalue())
                self.assertFalse(result['verified'])
                self.assertFalse(result['backup_created'])
                self.assertEqual(result['backup_ranges'], [])
                self.assertEqual(len(memory.pulses), 2 if action == 'write' else 1)
                with patch('spc5tool.cli.backend') as backend, contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(argv + ['--backup', str(Path(directory) / 'backup')]), 2)
                    backend.assert_not_called()
