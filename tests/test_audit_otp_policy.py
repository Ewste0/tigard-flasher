"""Customer OTP bounds and the separate, exact-slot provisioning path."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from spc5tool.chips import SPC5748G, SPC58NH92, SPC58EH92
from spc5tool.cli import main
from spc5tool.errors import NexusBusError, DriverError
from spc5tool.flash import Flash
from spc5tool.geometry import validate_program_range
from spc5tool.security import provision, password_address
from spc5tool.web import Workspace, catalog
from tests.simulator import SimMemory


C55 = (SPC5748G, SPC58NH92, SPC58EH92)


class OtpPolicyTests(unittest.TestCase):
    def test_reserved_and_crossing_program_ranges_fail_before_memory_access(self):
        for chip in C55:
            unit = chip.program_unit
            end = 0x404000 if chip == SPC5748G else 0x402000
            for address, size in ((0x400000, unit), (0x400120, 32),
                                  (0x400200, unit), (0x400300, unit),
                                  (0x401000 - unit, 2 * unit), (end - unit, 2 * unit)):
                with self.subTest(chip=chip.key, address=address, size=size):
                    memory = Mock()
                    with self.assertRaisesRegex(ValueError, "customer OTP|BAF is not writable"):
                        Flash(memory, chip).program(address, b"x" * size)
                    self.assertEqual(memory.mock_calls, [])
            for address in (0x401000, end - unit):
                validate_program_range(chip, address, unit)

    def test_api_and_cli_reject_reserved_otp_before_opening_usb(self):
        for chip in C55:
            with self.subTest(chip=chip.key), tempfile.TemporaryDirectory() as directory:
                workspace = Workspace(directory)
                info = workspace.add_file("reserved.bin", b"x" * 32)
                with patch.object(workspace, "adapter") as adapter:
                    with self.assertRaisesRegex(ValueError, "customer OTP"):
                        workspace.plan({"target": chip.key, "action": "program", "mode": "hardware",
                            "address": 0x400120, "file_id": info["id"], "expected_id": 0x0988101D})
                    adapter.assert_not_called()
                image = Path(directory) / "image.bin"
                image.write_bytes(b"x" * 32)
                with patch("spc5tool.cli.backend") as backend, \
                     contextlib.redirect_stderr(io.StringIO()) as error:
                    result = main(["program", chip.key, str(image), "--address", "0x400120"])
                self.assertEqual(result, 2)
                self.assertIn("customer OTP", error.getvalue())
                backend.assert_not_called()

    def test_all_five_password_slots_remain_exact_and_independent(self):
        for chip in C55:
            with self.subTest(chip=chip.key):
                memory = SimMemory(chip)
                flash = Flash(memory, chip)
                before = bytes(memory.regions[0x400000])
                expected = bytearray(before)
                for slot in ("jtag", "group0", "group1", "group2", "group3"):
                    password = bytes(range(32))
                    address = password_address(chip, slot)
                    backups = []
                    provision(flash, password, backups.extend, slot=slot)
                    self.assertEqual([(b.address, b.size, data) for b, data in backups],
                                     [(address, 32, b"\xff" * 32)])
                    expected[address - 0x400000:address - 0x400000 + 32] = password
                    self.assertEqual(memory.regions[0x400000], expected)
                with self.assertRaisesRegex(ValueError, "unknown password slot"):
                    provision(flash, bytes(range(32)), Mock(), slot="lifecycle")

    def test_spc58_default_read_is_customer_otp_and_revision_is_explicit(self):
        for chip in (SPC58NH92, SPC58EH92):
            info = next(t for t in catalog() if t["key"] == chip.key)
            preset = next(p for p in info["read_presets"] if p.get("region_default") == "UTEST")
            self.assertEqual((preset["address"], preset["size"]), (0x401000, 0x1000))
            region = next(r for r in info["regions"] if r["name"] == "UTEST")
            self.assertEqual(region["size"], 0x4000)  # Physical read map is retained.
            self.assertEqual(region["program_ranges"], ((0x401000, 0x1000),))
            self.assertIn("cut 2.0", chip.family)

    def test_spc58_diagnostic_ecc_fault_has_family_specific_guidance(self):
        for chip in (SPC58NH92, SPC58EH92):
            memory = SimMemory(chip)
            original = memory.read
            def read(address, size):
                if address == 0x4002A0:
                    memory.controllers[chip.flash_ctrl["FMC"]]["mcr"] |= 0x8000
                    raise NexusBusError("read", address, 0x10000006)
                return original(address, size)
            memory.read = read
            with self.assertRaisesRegex(DriverError, "SPC58 UTEST factory ECC"):
                Flash(memory, chip).read(0x4002A0, 16)

    def test_mpc5748_factory_edc_error_is_not_misreported_as_protection(self):
        memory = SimMemory(SPC5748G)
        def read(address, size):
            memory.controllers[0xFFFE0000]["mcr"] |= 0x10000000
            raise NexusBusError("read", address, 0x10000006)
        memory.read = read
        with self.assertRaisesRegex(DriverError, "MCR=0x10000600.*factory ECC"):
            Flash(memory, SPC5748G).read(0x400080, 8)


if __name__ == "__main__":
    unittest.main()
