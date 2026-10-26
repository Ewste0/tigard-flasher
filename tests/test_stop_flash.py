"""Stop never interrupts EHV or leaves volatile locks changed; no USB used."""
import unittest

from spc5tool.chips import CHIPS, SPC560B64, SPC5748G
from spc5tool.flash import Flash
from spc5tool.geometry import blocks_for
from spc5tool.errors import DriverError
from spc5tool.security import validate_shadow
from tests.test_batch_erase import RecordingMemory, lock_values


class StopRequested(Exception):
    pass


class StopFlashTests(unittest.TestCase):
    def setup_flash(self, chip, mode):
        memory = RecordingMemory(chip)
        flash = Flash(memory, chip)
        original_execute = memory._execute
        requested = []
        def execute(base, controller):
            original_execute(base, controller)
            if controller['mcr'] & mode:
                requested.append(True)  # Simulate a click while EHV is active.
        def checkpoint():
            if requested:
                self.assertTrue(all(not c['mcr'] & 0x15 for c in memory.controllers.values()),
                                'Stop was checked before clearing EHV and PGM/ERS')
                raise StopRequested()
        memory._execute = execute
        flash.cancel_check = checkpoint
        return memory, flash, lock_values(memory)

    def assert_idle_and_locked(self, memory, locks):
        self.assertEqual(lock_values(memory), locks)
        for controller in memory.controllers.values():
            self.assertTrue(controller['mcr'] & 0x400)
            self.assertFalse(controller['mcr'] & 0x15)

    def test_erase_stops_after_pulse_before_blank_check_all_profiles(self):
        for chip in CHIPS.values():
            with self.subTest(chip=chip.key):
                memory, flash, locks = self.setup_flash(chip, 4)
                block = next(b for b in blocks_for(chip) if b.region == 'CFLASH')
                memory.regions[block.address][:block.size] = b'\xA5' * block.size
                with self.assertRaises(StopRequested):
                    flash.erase(block.address, block.size)
                self.assert_idle_and_locked(memory, locks)
                self.assertEqual(len(memory.pulses), 1)
                self.assertFalse(any(a[0] == 'read' for a in memory.accesses))
                self.assertEqual(memory.read(block.address, block.size), b'\xff' * block.size)

    def test_program_stops_after_one_complete_ecc_unit_or_quad_page(self):
        for chip in CHIPS.values():
            for bulk in (False, True) if chip == SPC5748G else (False,):
                with self.subTest(chip=chip.key, bulk=bulk):
                    memory, flash, locks = self.setup_flash(chip, 0x10)
                    flash.bulk_program = bulk
                    block = next(b for b in blocks_for(chip) if b.region == 'CFLASH')
                    unit = 128 if bulk else chip.program_unit
                    with self.assertRaises(StopRequested):
                        flash.program(block.address, b'\xA5' * (unit * 2))
                    self.assert_idle_and_locked(memory, locks)
                    self.assertEqual(len(memory.pulses), 1)
                    self.assertEqual(memory.read(block.address, unit * 2), b'\xA5' * unit + b'\xff' * unit)

    def test_write_keeps_backup_and_does_not_start_next_program_pulse(self):
        for chip in CHIPS.values():
            with self.subTest(chip=chip.key):
                memory, flash, locks = self.setup_flash(chip, 0x10)
                block = next(b for b in blocks_for(chip) if b.region == 'CFLASH')
                memory.regions[block.address][:block.size] = b'\x12' * block.size
                backup = []
                with self.assertRaises(StopRequested):
                    flash.patch(block.address, b'\xA5' * (chip.program_unit * 2), backup.extend)
                self.assert_idle_and_locked(memory, locks)
                self.assertEqual(backup, [(block, b'\x12' * block.size)])
                self.assertEqual([p[1] for p in memory.pulses], [4, 0x10])
                unit = chip.program_unit
                self.assertEqual(memory.read(block.address, unit * 2), b'\xA5' * unit + b'\xff' * unit)

    def test_otp_stop_does_not_reuse_or_program_next_unit(self):
        for chip in CHIPS.values():
            with self.subTest(chip=chip.key):
                memory, flash, locks = self.setup_flash(chip, 0x10)
                address = 0x400000 if chip.algorithm == 'c90-b' else 0x401000
                unit = chip.program_unit
                with self.assertRaises(StopRequested):
                    flash.program(address, b'\xA5' * (unit * 2))
                self.assert_idle_and_locked(memory, locks)
                self.assertEqual(memory.read(address, unit * 2), b'\xA5' * unit + b'\xff' * unit)

    def test_shadow_stop_waits_for_valid_configuration_and_restored_data(self):
        for action in ('erase', 'write', 'program'):
            with self.subTest(action=action):
                memory, flash, locks = self.setup_flash(SPC560B64, 4 if action != 'program' else 0x10)
                memory.regions[0x200000][:16] = b'\x12' * 16
                original = bytes(memory.regions[0x200000])
                with self.assertRaises(StopRequested):
                    if action == 'erase':
                        flash.erase(0x200000, 0x4000)
                    elif action == 'write':
                        flash.patch(0x200000, b'\xA5' * 16, lambda _: None)
                    else:
                        flash.program(0x200010, b'\xA5' * 16)
                self.assert_idle_and_locked(memory, locks)
                actual = bytes(memory.regions[0x200000])
                self.assertEqual(actual[0x3DD0:], original[0x3DD0:])
                validate_shadow(actual)
                if action == 'write':
                    self.assertEqual(actual, b'\xA5' * 16 + original[16:])
                elif action == 'program':
                    self.assertEqual(actual[:32], original[:16] + b'\xA5' * 16)

    def test_cleanup_failure_overrides_cancel(self):
        memory, flash, _ = self.setup_flash(SPC5748G, 0x10)
        write32 = memory.write32
        def fail_restore(address, value):
            if memory.pulses and address == 0xFFFE0010 and value == 0xFFFFFFFF:
                raise DriverError('injected restore failure')
            write32(address, value)
        memory.write32 = fail_restore
        with self.assertRaisesRegex(DriverError, 'restore flash locks'):
            flash.program(0xF90000, b'\xA5' * 16)
        self.assertTrue(flash.poisoned)
