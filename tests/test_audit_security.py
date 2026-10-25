"""Security regressions against RM0037 Table 15 and Figures 10–11."""
import unittest
from unittest.mock import Mock

from spc5tool.chips import SPC560B54, SPC560B60, SPC560B64, SPC5748G, SPC58NH92, SPC58EH92
from spc5tool.flash import Flash
from spc5tool.security import installation_message, provision, validate_shadow
from tests.simulator import SimMemory


class ShadowSecurityAuditTests(unittest.TestCase):
    def test_password_installation_requires_power_cycle_on_every_family(self):
        for chip in (SPC560B54, SPC560B60, SPC560B64, SPC5748G, SPC58NH92, SPC58EH92):
            with self.subTest(chip=chip.key):
                message = installation_message(chip)
                self.assertIn("Power-cycle the target before testing password login", message)
                self.assertIn("protection was not activated", message)
                if not chip.password_protocol:
                    self.assertIn("JTAG login is not yet supported", message)

    @staticmethod
    def shadow(word, *, second=None, password=b"\xff" * 8):
        data = bytearray(b"\xff" * 0x4000)
        data[0x3DD8:0x3DE0] = password
        data[0x3DE0:0x3DE4] = word.to_bytes(4, "big")
        data[0x3DE4:0x3DE8] = (word if second is None else second).to_bytes(4, "big")
        return data

    def test_uncensored_configuration_accepts_every_serial_control_mode(self):
        # CW=0x55AA keeps JTAG uncensored; SC selects public/private serial access.
        # The private-password halfword rules do not apply to uncensored access.
        for serial_control in (0, 0x1234, 0x55AA, 0xFFFF):
            with self.subTest(serial_control=serial_control):
                validate_shadow(self.shadow((serial_control << 16) | 0x55AA))

    def test_censored_configuration_requires_private_serial_access_and_valid_password(self):
        for censorship_control in (0, 0x1234, 0xFFFF):
            with self.subTest(censorship_control=censorship_control):
                word = (0x55AA << 16) | censorship_control
                validate_shadow(self.shadow(word, password=bytes.fromhex("123456789ABCDEF0")))
                with self.assertRaisesRegex(ValueError, "halfword"):
                    validate_shadow(self.shadow(word))

    def test_lockout_configurations_remain_rejected(self):
        for first, second in ((0x12345678, 0x12345678),
                              (0xFFFFFFFF, 0xFFFFFFFF),
                              (0x55AA55AA, 0x123455AA),
                              (0x55AA0000, 0x55AA0001)):
            with self.subTest(first=first, second=second):
                with self.assertRaisesRegex(ValueError, "lock out"):
                    validate_shadow(self.shadow(first, second=second))

    def test_erase_and_password_install_preserve_public_serial_configuration(self):
        for chip in (SPC560B54, SPC560B60, SPC560B64):
            for action in ("erase", "install"):
                with self.subTest(chip=chip.key, action=action):
                    memory = SimMemory(chip)
                    region = memory.regions[0x200000]
                    region[:64] = b"x" * 64
                    region[0x3DE0:0x3DE8] = bytes.fromhex("123455AA123455AA")
                    before = bytes(region)
                    flash = Flash(memory, chip)
                    if action == "erase":
                        flash.erase(0x200000, 0x4000)
                        self.assertEqual(region[:0x3DD0], b"\xff" * 0x3DD0)
                        self.assertEqual(region[0x3DD0:], before[0x3DD0:])
                    else:
                        backup = Mock()
                        password = bytes.fromhex("123456789ABCDEF0")
                        provision(flash, password, backup)
                        backup.assert_called_once()
                        self.assertEqual(region[:0x3DD8], before[:0x3DD8])
                        self.assertEqual(region[0x3DD8:0x3DE0], password)
                        self.assertEqual(region[0x3DE0:], before[0x3DE0:])


if __name__ == "__main__":
    unittest.main()
