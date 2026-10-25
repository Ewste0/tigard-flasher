"""Regressions for the MPC5748G factory ECC test area; never opens USB."""
import contextlib
import io
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from spc5tool.chips import SPC5748G, SPC58NH92
from spc5tool.cli import main
from spc5tool.errors import DriverError, NexusBusError
from spc5tool.flash import Flash
from spc5tool.geometry import backup_blocks
from spc5tool.memory import NexusMemory
from spc5tool.security import provision
from spc5tool.web import Workspace
from tests.simulator import SimMemory


class FactoryEccMemory(SimMemory):
    def read(self, address, size):
        if address < 0x4000A0 and address + size > 0x400060:
            self.controllers[self.chip.flash_ctrl["FMC"]]["mcr"] |= 0x8000
            raise NexusBusError("read", max(address, 0x400060), 0x10000006)
        return super().read(address, size)


class UtestTests(unittest.TestCase):
    def test_nexus_reports_failed_word_and_direction_without_reading_stale_data(self):
        for operation in ("read", "write"):
            once = Mock(chip=SPC5748G)
            once.nexus_read.return_value = 0x10000006
            memory = NexusMemory(once)
            with self.assertRaises(NexusBusError) as caught:
                if operation == "read":
                    memory.read32(0x400060)
                else:
                    memory.write32(0x400060, 0)
            self.assertEqual((caught.exception.address, caught.exception.operation, caught.exception.status),
                             (0x400060, operation, 0x10000006))
            self.assertIn("0x00400060", str(caught.exception))
            once.nexus_read.assert_called_once_with(7)

    def test_full_utest_read_explains_factory_ecc_and_preset_reads_exact_bytes(self):
        memory = FactoryEccMemory(SPC5748G)
        flash = Flash(memory, SPC5748G)
        with self.assertRaisesRegex(DriverError, "0x00400060.*MCR=0x00008600.*factory ECC"):
            flash.read(0x400000, 0x4000)
        payload = bytes(range(256)) * 64
        memory.regions[0x400000][:] = payload
        for key in ("utest", "utest_user"):
            preset = next(p for p in SPC5748G.read_presets if p["key"] == key)
            offset = preset["address"] - 0x400000
            self.assertEqual(flash.read(preset["address"], preset["size"]),
                             payload[offset:offset + preset["size"]])

    def test_non_ecc_bus_error_is_not_reported_as_factory_test(self):
        memory = SimMemory(SPC5748G)
        flash = Flash(memory, SPC5748G)
        memory.read = Mock(side_effect=NexusBusError("read", 0x400120, 0x10000006))
        with self.assertRaisesRegex(DriverError, "0x00400120.*read protection") as caught:
            flash.read(0x400120, 32)
        self.assertNotIn("factory ECC", str(caught.exception))
        memory.read32 = Mock(side_effect=DriverError("diagnostics unavailable"))
        # Failure while inspecting MCR must preserve the original bus failure.
        with patch.object(flash, "_idle"), patch.object(memory, "write32"):
            with self.assertRaises(NexusBusError):
                flash.read(0x400120, 32)

    def test_otp_backup_covers_units_but_cflash_retains_whole_blocks(self):
        for chip, expected in ((SPC5748G, (0x401008, 8)), (SPC58NH92, (0x401000, 16))):
            b, = backup_blocks(chip, 0x401009, 2)
            self.assertEqual((b.address, b.size), expected)
        b, = backup_blocks(SPC5748G, 0xFA8008, 8)
        self.assertEqual((b.address, b.size), (0xFA8000, 0x4000))

    def test_password_backup_avoids_factory_tests_and_precedes_any_programming(self):
        for chip in (SPC5748G, SPC58NH92):
            memory = FactoryEccMemory(chip)
            before = bytes(memory.regions[0x400000])
            flash = Flash(memory, chip)
            def backup(originals):
                self.assertEqual(bytes(memory.regions[0x400000]), before)
                block, data = originals[0]
                self.assertEqual((block.address, block.size, data), (0x400120, 32, b"\xff" * 32))
                self.assertEqual(len(originals), 1)
            provision(flash, bytes(range(32)), backup)
            self.assertEqual(memory.regions[0x400000][0x120:0x140], bytes(range(32)))
            self.assertEqual(memory.regions[0x400000][:0x120], before[:0x120])
            self.assertEqual(memory.regions[0x400000][0x140:], before[0x140:])
            fresh = FactoryEccMemory(chip)
            with self.assertRaisesRegex(OSError, "disk full"):
                provision(Flash(fresh, chip), bytes(range(32)), Mock(side_effect=OSError("disk full")))
            self.assertEqual(fresh.regions[0x400000], before)
            self.assertFalse(any(a == chip.flash_ctrl["FMC"] and v & 1 for a, v in fresh.log))

    @patch('spc5tool.web.discover_tigards')
    def test_web_program_and_password_backups_use_actual_spans(self, discovery):
        from tests.backends import tigard_descriptor
        discovery.return_value = [tigard_descriptor()]
        for action, address, size in (("program", 0x401000, 16), ("install_password", 0x400120, 32)):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                workspace = Workspace(directory)
                memory = FactoryEccMemory(SPC5748G)
                flash = Flash(memory, SPC5748G)
                @contextlib.contextmanager
                def backend(plan, progress):
                    flash.progress = progress
                    yield flash
                workspace._backend = backend
                request = dict(target="spc5748g", action=action, mode="hardware", expected_id=0x0988101D,
                               address=address, length=size)
                if action == "program":
                    request["file_id"] = workspace.add_file("otp.bin", bytes(range(size)), "upload")["id"]
                else:
                    request.update(new_password="01234567" * 8, confirm_password="01234567" * 8)
                plan = workspace.plan(request)
                self.assertEqual(plan["backup_bytes"], size)
                self.assertEqual([(b["address"], b["size"]) for b in plan["backup_ranges"]], [(address, size)])
                job = workspace.start(plan["id"])
                deadline = time.monotonic() + 5
                while not job.get("finished") and time.monotonic() < deadline:
                    time.sleep(0.01)
                    job = workspace.snapshot(job["id"])
                self.assertEqual(job["status"], "complete", job)
                _, path = workspace.file(job["result"]["backup"]["id"])
                with zipfile.ZipFile(path) as archive:
                    entry, = json.loads(archive.read("manifest.json"))["blocks"]
                    self.assertEqual((entry["address"], entry["size"]), (address, size))
                    self.assertEqual(archive.read(entry["file"]), b"\xff" * size)

    def test_cli_program_backup_avoids_factory_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "otp.bin"
            image.write_bytes(bytes(range(8)))
            backup = Path(directory) / "backup"
            flash = Flash(FactoryEccMemory(SPC5748G), SPC5748G)
            with patch("spc5tool.cli.backend", return_value=contextlib.nullcontext(flash)), contextlib.redirect_stdout(io.StringIO()):
                result = main(["program", "mpc5748g", str(image), "--address", "0x401000",
                               "--execute", "--backup", str(backup), "--expect-idcode", "0x0988101D"])
            self.assertEqual(result, 0)
            entry, = json.loads((backup / "manifest.json").read_text())["blocks"]
            self.assertEqual((entry["address"], entry["size"]), (0x401000, 8))
            self.assertEqual((backup / entry["file"]).read_bytes(), b"\xff" * 8)


if __name__ == "__main__":
    unittest.main()
