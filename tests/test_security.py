"""Security/OTP regression tests use a controller double, never USB."""
import json
import io
import contextlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from spc5tool.chips import SPC560B54, SPC560B60, SPC560B64, SPC5748G, SPC58NH92, SPC58EH92
from spc5tool.flash import Flash
from spc5tool.geometry import blocks_for
from spc5tool.security import parse_password, provision, validate_shadow
from spc5tool.once import OnCE
from spc5tool.web import Workspace
from spc5tool.cli import main
from spc5tool.session import TargetSession
from tests.simulator import SimMemory


class SecurityTests(unittest.TestCase):
    def test_lengths_and_spc560_halfword_rules(self):
        self.assertEqual(parse_password(SPC560B64, 'FEEDFACECAFEBEEF'), bytes.fromhex('FEEDFACECAFEBEEF'))
        self.assertEqual(len(parse_password(SPC5748G, '01234567' * 8)), 32)
        for chip, value in ((SPC560B64, '1' * 64), (SPC5748G, '1' * 16), (SPC5748G, '1' * 8), (SPC58NH92, 'G' * 64)):
            with self.assertRaises(ValueError): parse_password(chip, value)
        for value in ('0000123456789ABC', '1234FFFF56789ABC'):
            with self.assertRaises(ValueError): parse_password(SPC560B64, value, installing=True)

    def test_extra_chip_capacities_and_cores(self):
        for chip, size in ((SPC560B54, 0xC0000), (SPC560B60, 0x100000), (SPC560B64, 0x180000), (SPC58EH92, 0xA00000)):
            blocks = [b for b in blocks_for(chip) if b.region == 'CFLASH']
            self.assertEqual(sum(b.size for b in blocks), size)
        self.assertEqual(SPC58EH92.core_selectors, (0x2A, 0x28))

    def test_otp_program_read_verify_blank_and_no_erase(self):
        for chip, address in ((SPC560B64, 0x400000), (SPC560B64, 0xC00000), (SPC5748G, 0x400300), (SPC58NH92, 0x400300)):
            with self.subTest(chip=chip.key, address=address):
                memory = SimMemory(chip)
                flash = Flash(memory, chip)
                payload = bytes(range(chip.program_unit))
                flash.verify(address, b'\xff' * len(payload))
                flash.program(address, payload)
                flash.verify(address, payload)
                self.assertEqual(flash.read(address, len(payload)), payload)
                with self.assertRaisesRegex(Exception, 'erased flash'):
                    flash.program(address, payload)
                memory.log.clear()
                b = next(b for b in blocks_for(chip) if b.address <= address < b.end)
                with self.assertRaisesRegex(ValueError, 'does not support erase'):
                    flash.erase(b.address, b.size)
                with self.assertRaisesRegex(ValueError, 'Program once'):
                    flash.patch(address, payload, Mock())
                self.assertEqual(memory.log, [])

    def test_shadow_erase_restores_configuration_before_return(self):
        memory = SimMemory(SPC560B64)
        memory.regions[0x200000][:100] = b'X' * 100
        before = bytes(memory.regions[0x200000])
        flash = Flash(memory, SPC560B64)
        flash.erase(0x200000, 0x4000)
        actual = flash.read(0x200000, 0x4000)
        self.assertEqual(actual[:0x3DD0], b'\xff' * 0x3DD0)
        self.assertEqual(actual[0x3DD0:], before[0x3DD0:])
        validate_shadow(actual)
        self.assertIn((0xC3F88010, 1 << 20), memory.log)

    def test_invalid_shadow_patch_never_erases(self):
        memory = SimMemory(SPC560B64)
        flash = Flash(memory, SPC560B64)
        backup = Mock()
        with self.assertRaisesRegex(ValueError, 'lock out'):
            flash.patch(0x203DE0, b'\x00' * 8, backup)
        self.assertFalse(any(a == 0xC3F88000 and v & 5 == 5 for a,v in memory.log))
        validate_shadow(memory.regions[0x200000])

    def test_password_provision_preserves_surrounding_bytes_and_dcf(self):
        for chip in (SPC560B64, SPC5748G, SPC58NH92):
            memory = SimMemory(chip)
            flash = Flash(memory, chip)
            backup = Mock()
            data = bytes.fromhex('123456789ABCDEF0') if chip == SPC560B64 else bytes(range(32))
            address = 0x203DD8 if chip == SPC560B64 else 0x400120
            region = memory.regions[0x200000 if chip == SPC560B64 else 0x400000]
            before = bytes(region)
            provision(flash, data, backup)
            offset = address - (0x200000 if chip == SPC560B64 else 0x400000)
            self.assertEqual(region[:offset], before[:offset])
            self.assertEqual(region[offset:offset+len(data)], data)
            self.assertEqual(region[offset+len(data):], before[offset+len(data):])
            backup.assert_called_once()

    def test_c90_password_scan_occurs_in_reset_before_once(self):
        tap = Mock()
        once = OnCE(tap, SPC560B64)
        with patch.object(once, 'wait_debug', return_value=0x209):
            once.attach(bytes.fromhex('FEEDFACECAFEBEEF'))
        calls = tap.mock_calls
        from unittest.mock import call
        password_scan = call.scan_dr(65, (1 << 64) | 0xCAFEBEEFFEEDFACE, read=False)
        self.assertIn(password_scan, calls)
        self.assertLess(calls.index(call.io.set_reset(srst=True)), calls.index(password_scan))
        self.assertLess(calls.index(password_scan), calls.index(call.io.set_reset(srst=False)))

    def test_password_never_appears_in_plan_or_status(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(directory)
            password = '01234567' * 8
            plan = workspace.plan(dict(target='spc5748g', action='install_password', mode='hardware', expected_id=0x0988101D,
                                       new_password=password, confirm_password=password))
            self.assertNotIn(password, json.dumps(plan))
            self.assertNotIn(password, json.dumps(workspace.status()))
            self.assertEqual((plan['address'], plan['length']), (0x400120, 32))
            with self.assertRaisesRegex(ValueError, '64 hexadecimal'):
                workspace.plan(dict(target='spc5748g', action='install_password', mode='hardware', expected_id=0x0988101D,
                                    new_password='12345678', confirm_password='12345678'))

    def test_multiblock_erase_and_write_progress_do_not_restart(self):
        memory = SimMemory(SPC5748G)
        events = []
        flash = Flash(memory, SPC5748G, progress=lambda p,d,t: events.append((p,d/t)))
        for work in (lambda: flash.erase(0xFA0000, 0x10000), lambda: flash.patch(0xFABFF8, bytes(range(16)), Mock())):
            events.clear()
            work()
            fractions = [fraction for _,fraction in events]
            self.assertEqual(fractions, sorted(fractions))
            self.assertEqual(fractions[-1], 1)
            self.assertTrue(any('blank' in phase or 'program' in phase for phase,_ in events))

    def test_cli_password_plan_does_not_disclose_secret_or_open_usb(self):
        with tempfile.TemporaryDirectory() as directory:
            password = '01234567' * 8
            path = Path(directory) / 'password.txt'
            path.write_text(password)
            output = io.StringIO()
            with patch('spc5tool.session.MpsseTransport.open') as opened, contextlib.redirect_stdout(output):
                self.assertEqual(main(['install-password', 'spc58nh92', '--new-password-file', str(path)]), 0)
            opened.assert_not_called()
            self.assertNotIn(password, output.getvalue())
            self.assertEqual(json.loads(output.getvalue())['bytes'], 32)

    def test_unverified_c55_login_rejected_before_usb(self):
        with patch('spc5tool.session.MpsseTransport.open') as opened:
            with self.assertRaisesRegex(ValueError, 'reference manual'):
                with TargetSession(SPC58NH92, password=bytes(range(32))):
                    pass
            opened.assert_not_called()

if __name__ == '__main__': unittest.main()
