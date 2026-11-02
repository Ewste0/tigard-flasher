"""MPC5668 physical-map, controller and complete wire-path offline contracts."""
import unittest
from unittest.mock import Mock, patch

from spc5tool.chips import SPC5668G
from spc5tool.errors import DriverError, NexusBusError, VerificationError
from spc5tool.flash import Flash
from spc5tool.geometry import blocks_for, select_blocks, speed_blocks
from spc5tool.mpsse import MpsseTransport
from spc5tool.security import password_address, provision, validate_shadow
from spc5tool.session import TargetSession
from tests.fake_mpsse import FakeMpsse
from tests.model_mpc5668 import Mpc5668Target
from tests.test_batch_erase import RecordingMemory, lock_values
from tests import test_stop_flash
from tests.test_stop_flash import StopRequested


class Mpc5668Tests(unittest.TestCase):
    def test_physical_geometry_matches_manual_not_pe_effective_alias(self):
        blocks = blocks_for(SPC5668G)
        self.assertEqual(len(blocks), 19)
        code = [b for b in blocks if b.region == 'CFLASH']
        self.assertEqual([b.size for b in code], [0x4000] * 8 + [0x10000] * 2 + [0x20000] * 2 + [0x40000] * 6)
        self.assertEqual([b.mask for b in code], [1 << i for i in range(10)] + [0x10000, 0x20000] + [1 << i for i in range(6)])
        self.assertEqual(code[0].address, 0)
        self.assertEqual(code[-1].end, 0x200000)
        self.assertEqual((blocks[-1].address, blocks[-1].size, blocks[-1].mask), (0xFFC000, 0x4000, 0x100000))
        self.assertEqual({b.controller for b in blocks}, {0xFFFF8000})
        for address in (0x200000, 0x400000, 0x800000, 0xC00000, 0x30000000, 0x30FFC000):
            with self.subTest(address=hex(address)), self.assertRaises(ValueError):
                select_blocks(SPC5668G, address, 8)
        with self.assertRaisesRegex(ValueError, 'only for SPC560'):
            speed_blocks(SPC5668G, 0, 0x200000)

    def test_password_operations_reject_before_usb_or_array_mutation(self):
        with patch('spc5tool.session.MpsseTransport.open') as opened:
            with self.assertRaises(ValueError):
                TargetSession(SPC5668G, password=bytes.fromhex('FEEDFACECAFEBEEF'))
            opened.assert_not_called()
        memory = RecordingMemory(SPC5668G)
        with self.assertRaisesRegex(ValueError, 'serial-boot'):
            password_address(SPC5668G)
        with self.assertRaisesRegex(ValueError, 'verified security map'):
            provision(Flash(memory, SPC5668G), bytes.fromhex('123456789ABCDEF0'), Mock())
        self.assertEqual(memory.log, [])

    def test_page_programming_uses_16_bytes_only_within_page_and_nonblank_units(self):
        for address, data, expected in (
                (0, bytes(range(32)), [(0, 16), (16, 16)]),
                (8, bytes(range(24)), [(8, 8), (16, 16)]),
                (0, b'\xA5' * 8 + b'\xff' * 8 + b'\x5A' * 8, [(0, 8), (16, 8)]),
                (0x3FF8, bytes(range(24)), [(0x3FF8, 8), (0x4000, 16)])):
            with self.subTest(address=address, length=len(data)):
                memory = RecordingMemory(SPC5668G)
                loaded = []
                execute = memory._execute
                def capture(base, controller):
                    loaded.append((controller['interlock'], len(controller['pending']) * 4))
                    execute(base, controller)
                memory._execute = capture
                flash = Flash(memory, SPC5668G)
                before = lock_values(memory)
                flash.program(address, data)
                self.assertEqual(memory.read(address, len(data)), data)
                self.assertEqual(loaded, expected)
                self.assertEqual(lock_values(memory), before)

    def test_shadow_one_ccw_and_general_word_not_duplicate(self):
        memory = RecordingMemory(SPC5668G)
        original = bytes(memory.regions[0xFFC000])
        validate_shadow(original, SPC5668G)
        self.assertNotEqual(original[0x3DE0:0x3DE4], original[0x3DE4:0x3DE8])
        for serial_control in (0, 0x55AA, 0xFFFF):
            valid = bytearray(original)
            valid[0x3DE2:0x3DE4] = serial_control.to_bytes(2, 'big')
            validate_shadow(valid, SPC5668G)
        for censorship in (0, 0xAA55, 0xFFFF):
            invalid = bytearray(original)
            invalid[0x3DE0:0x3DE2] = censorship.to_bytes(2, 'big')
            with self.assertRaisesRegex(ValueError, 'disable normal debug'):
                validate_shadow(invalid, SPC5668G)

    def test_shadow_erase_restores_entire_tail_and_never_erases_main(self):
        memory = RecordingMemory(SPC5668G)
        memory.regions[0][:16] = b'MAIN PRESERVED!!'
        memory.regions[0xFFC000][:16] = b'\x12' * 16
        memory.regions[0xFFC000][0x3DE8:] = bytes(range(256)) * 2 + bytes(range(24))
        original = bytes(memory.regions[0xFFC000])
        locks = lock_values(memory)
        Flash(memory, SPC5668G).erase(0xFFC000, 0x4000)
        actual = bytes(memory.regions[0xFFC000])
        self.assertEqual(actual, b'\xff' * 0x3DD0 + original[0x3DD0:])
        self.assertEqual(memory.regions[0][:16], b'MAIN PRESERVED!!')
        self.assertEqual(lock_values(memory), locks)
        self.assertEqual([p[2] for p in memory.pulses if p[1] == 4], [0xFFC000])
        self.assertNotIn((0xFFFF8010, 0x100000), memory.log)

    def test_shadow_censorship_change_rejected_before_first_erase(self):
        memory = RecordingMemory(SPC5668G)
        flash = Flash(memory, SPC5668G)
        with self.assertRaisesRegex(ValueError, 'disable normal debug'):
            flash.patch(0xFFFDE0, b'\xff' * 4, Mock())
        self.assertEqual(memory.pulses, [])
        # General-purpose word +3DE4 may differ without corrupting the CCW.
        flash.patch(0xFFFDE4, b'abcd', lambda _: None)
        self.assertEqual(memory.read(0xFFFDE0, 8), bytes.fromhex('55AA55AA') + b'abcd')

    def wire_session(self, **kwargs):
        target = Mpc5668Target(**kwargs)
        backend = FakeMpsse(0)
        backend.tgt = target
        backend.close = Mock()
        return target, backend, MpsseTransport(backend)

    def test_complete_session_reset_nexus_program_verify_trace(self):
        target, backend, transport = self.wire_session()
        locks = {o: target.flash.controllers[0xFFFF8000]['regs'][o] for o in (4, 8, 12)}
        with patch('spc5tool.session.MpsseTransport.open', return_value=transport), \
                patch('spc5tool.once.time.sleep'):
            with TargetSession(SPC5668G) as session:
                self.assertEqual(session.idcode.raw, 0x1826801D)
                self.assertEqual(target.halted, {0x11, 0x19})
                self.assertEqual(target.core, 0x11)
                session.flash.program(0, bytes(range(16)))
                self.assertEqual(session.flash.read(0, 16), bytes(range(16)))
                self.assertEqual(session.flash.read(0xFFFDE0, 8), bytes.fromhex('55AA55AA12345678'))
        self.assertEqual([e[2] for e in target.trace if e[:2] == ('ir', 5) and e[2] in (0x11, 0x19)],
                         [0x11, 0x19, 0x11, 0x19, 0x11])
        reset = [i for i, e in enumerate(target.trace) if e[0] == 'reset']
        for core in (0x11, 0x19):
            arm = target.trace.index(('core', core, 0x12, 5))
            self.assertLess(reset[-2], arm)
            self.assertLess(arm, reset[-1])
            self.assertIn(('core', core, 0x31, 0x80000000), target.trace)
            self.assertIn(('core', core, 0x30, 0xFFFFFFFF), target.trace)
        self.assertEqual(target.peripherals[0xFFF38000], 0xFF00000A)
        self.assertEqual(target.peripherals[0xFFFF801C], 0x0800FF00)
        self.assertEqual(target.peripherals[0xFFFF8020], 0x3000FF00)
        self.assertEqual({o: target.flash.controllers[0xFFFF8000]['regs'][o] for o in locks}, locks)
        self.assertFalse(any(e[0] == 'bus' and 0x20000000 <= e[2] < 0x40000000 for e in target.trace))
        backend.close.assert_called_once()

    def test_secondary_held_in_reset_is_quiescent_and_primary_restored(self):
        target, _, transport = self.wire_session(secondary_reset=True)
        with patch('spc5tool.session.MpsseTransport.open', return_value=transport), patch('spc5tool.once.time.sleep'):
            with TargetSession(SPC5668G) as session:
                self.assertEqual(session.flash.debug_states['core1']['state'], 'held in reset')
                self.assertEqual(target.core, 0x11)
                self.assertEqual(session.flash.read(0, 8), b'\xff' * 8)

    def test_wrong_part_rejected_before_reset_or_flash_write(self):
        for identity in (0x0988101D, 0x01146041, 0x1826901D):
            target, backend, transport = self.wire_session(idcode=identity)
            with patch('spc5tool.session.MpsseTransport.open', return_value=transport):
                with self.assertRaisesRegex(DriverError, 'IDCODE mismatch'):
                    with TargetSession(SPC5668G):
                        self.fail('wrong MCU accepted')
            self.assertEqual(target.flash.log, [])
            self.assertFalse(any(e[0] == 'core' for e in target.trace))
            backend.close.assert_called_once()

    def test_nexus_error_never_returns_data_as_success(self):
        target, _, transport = self.wire_session()
        with patch('spc5tool.session.MpsseTransport.open', return_value=transport), patch('spc5tool.once.time.sleep'):
            with TargetSession(SPC5668G) as session:
                target.bus_error = 0xFFC000
                with self.assertRaises(NexusBusError):
                    session.memory.read32(0xFFC000)

    def test_locked_and_failed_page_never_report_program_success(self):
        for fault in ('locked', 'peg', 'corrupt'):
            with self.subTest(fault=fault):
                memory = RecordingMemory(SPC5668G)
                memory.lock_stuck = fault == 'locked'
                memory.fault = None if fault == 'locked' else fault
                with self.assertRaises((DriverError, VerificationError)):
                    Flash(memory, SPC5668G).program(0, bytes(range(16)))
                self.assertFalse(memory.controllers[0xFFFF8000]['mcr'] & 0x15)
                if fault == 'locked':
                    self.assertEqual(memory.pulses, [])

    def test_shadow_stop_deferred_until_configuration_and_user_data_restored(self):
        helper = test_stop_flash.StopFlashTests()
        for action in ('erase', 'write'):
            with self.subTest(action=action):
                memory, flash, locks = helper.setup_flash(SPC5668G, 4)
                original = bytes(memory.regions[0xFFC000])
                with self.assertRaises(StopRequested):
                    if action == 'erase':
                        flash.erase(0xFFC000, 0x4000)
                    else:
                        flash.patch(0xFFC000, b'changed!', lambda _: None)
                helper.assert_idle_and_locked(memory, locks)
                actual = bytes(memory.regions[0xFFC000])
                self.assertEqual(actual[0x3DD0:], original[0x3DD0:])
                validate_shadow(actual, SPC5668G)
                if action == 'write':
                    self.assertEqual(actual[:8], b'changed!')
