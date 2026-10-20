"""Password-session tests. Public synthetic credentials only; never opens USB."""
import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from spc5tool.chips import SPC5748G, SPC58NH92
from spc5tool.cli import main
from spc5tool.errors import DriverError, NexusBusError
from spc5tool.flash import Flash
from spc5tool.mpsse import MpsseTransport
from spc5tool.once import OnCE
from spc5tool.security import login_result, installation_message
from spc5tool.session import TargetSession
from spc5tool.tap import JtagTap
from spc5tool.web import Workspace, catalog
from tests.fake_mpsse import FakeMpsse, FakeTarget

PASSWORD = bytes(range(32))


class PasswordRegister(FakeTarget):
    """Capture the physical 256-bit DR after instruction 7, using the TAP model."""
    def __init__(self):
        self.instruction = 1
        self.password_scans = []
        super().__init__(0x0988101D, 6)

    def _enter(self, nxt):
        if nxt == "IRUPDATE":
            self.instruction = self.ir_shift
        if nxt == "DRCAPTURE" and self.instruction == 7:
            self.dr, self.dr_width = 0, 256
            self.state = nxt
            return
        if nxt == "DRUPDATE" and self.instruction == 7:
            self.password_scans.append(self.dr)
        super()._enter(nxt)


class PasswordLoginTests(unittest.TestCase):
    def test_c55_scan_is_after_reset_release_before_core_access(self):
        tap = Mock()
        once = OnCE(tap, SPC5748G)
        with patch.object(once, "wait_debug", return_value=0x209), patch("spc5tool.once.time.sleep"):
            once.attach(PASSWORD)
        scans = [c for c in tap.mock_calls if c[0] == "scan_dr" and c.args[0] >= 64]
        expected = int("1c1d1e1f18191a1b14151617101112130c0d0e0f08090a0b0405060700010203", 16)
        self.assertEqual(scans, [call.scan_dr(256, expected, read=False)])
        events = tap.mock_calls
        self.assertLess(events.index(call.io.set_reset(srst=False)), events.index(scans[0]))
        self.assertLess(events.index(scans[0]), events.index(call.scan_ir(6, 0x28)))
        tap.reset.assert_called_once()
        self.assertLess(events.index(call.reset()), events.index(scans[0]))

    def test_wide_password_survives_mpsse_last_bit_and_update_dr(self):
        fake = FakeMpsse(0x0988101D, 6)
        fake.tgt = PasswordRegister()
        once = OnCE(JtagTap(MpsseTransport(fake)), SPC5748G)
        # Final scan bit is one, exercising the separate TMS exit-bit command.
        vector = PASSWORD[:-4] + bytes.fromhex("89ABCDEF")
        with patch.object(once, "wait_debug", return_value=0x209), patch("spc5tool.once.time.sleep"):
            once.attach(vector)
        expected = int("89abcdef18191a1b14151617101112130c0d0e0f08090a0b0405060700010203", 16)
        self.assertEqual(fake.tgt.password_scans, [expected])

    def test_bad_length_or_unsupported_family_never_resets_or_opens_usb(self):
        for chip, value in ((SPC5748G, b"x" * 8), (SPC58NH92, PASSWORD)):
            tap = Mock()
            with patch("spc5tool.session.MpsseTransport.open") as opened:
                with self.assertRaises(ValueError):
                    TargetSession(chip, password=value)
                with self.assertRaises(ValueError):
                    OnCE(tap, chip).attach(value)
                opened.assert_not_called()
                self.assertEqual(tap.mock_calls, [])

    def test_password_scan_failure_does_not_select_a_core(self):
        tap = Mock()
        tap.scan_dr.side_effect = DriverError("transport failure")
        with self.assertRaises(DriverError), patch("spc5tool.once.time.sleep"):
            OnCE(tap, SPC5748G).attach(PASSWORD)
        self.assertEqual(tap.scan_ir.call_args_list, [call(6, 7)])
        tap.io.set_reset.assert_called_with(srst=False)

    def test_hardware_status_is_distinct_from_readback_match(self):
        memory = Mock()
        memory.read32.return_value = 0xE0000002
        result = login_result(memory, SPC5748G, PASSWORD)
        self.assertEqual(result["method"], "jtag_status")
        self.assertTrue(result["password_verified"])
        self.assertTrue(result["unlock_verified"])
        memory.read.assert_not_called()
        memory.read32.return_value = 0xA0000003
        memory.read.return_value = PASSWORD
        result = login_result(memory, SPC5748G, PASSWORD)
        memory.read.assert_called_once_with(0x400120, 32)
        self.assertEqual(result["method"], "stored_password")
        self.assertTrue(result["password_verified"])
        self.assertFalse(result["unlock_verified"])
        self.assertIn("protected-target unlock was not verified", result["message"])
        memory.write32.assert_not_called()
        self.assertNotIn(PASSWORD.hex(), json.dumps(result))

    def test_wrong_password_and_protected_state_fail_without_disclosing_bytes(self):
        memory = Mock()
        memory.read32.return_value = 0xA0000003
        memory.read.return_value = b"\xa5" * 32
        with self.assertRaisesRegex(DriverError, "does not match") as caught:
            login_result(memory, SPC5748G, PASSWORD)
        self.assertNotIn(PASSWORD.hex(), str(caught.exception))
        self.assertNotIn("a5", str(caught.exception))
        memory.read.reset_mock()
        for lifecycle in (0, 2, 6, 7):
            memory.read32.return_value = 0xA0000000 | lifecycle
            with self.assertRaisesRegex(DriverError, "not accepted"):
                login_result(memory, SPC5748G, PASSWORD)
            memory.read.assert_not_called()
        memory.read32.return_value = 0xA0000003
        memory.read.side_effect = NexusBusError("read", 0x400120, 0x10000006)
        with self.assertRaises(NexusBusError):
            login_result(memory, SPC5748G, PASSWORD)

    def test_failed_login_closes_transport_before_watchdog_or_flash_setup(self):
        with patch("spc5tool.session.MpsseTransport.open") as opened, \
             patch("spc5tool.session.JtagTap") as tap, \
             patch("spc5tool.session.OnCE"), \
             patch("spc5tool.session.NexusMemory") as memory, \
             patch("spc5tool.session.Flash") as flash:
            tap.return_value.read_idcode.return_value = 0x0988101D
            memory.return_value.read32.return_value = 0xA0000003
            memory.return_value.read.return_value = b"\xff" * 32
            with self.assertRaisesRegex(DriverError, "does not match"):
                with TargetSession(SPC5748G, password=PASSWORD):
                    self.fail("rejected login must not open a session")
            memory.return_value.write32.assert_not_called()
            flash.assert_not_called()
            opened.return_value.close.assert_called_once()

    def test_authentication_only_does_not_configure_flash_or_watchdogs(self):
        with patch("spc5tool.session.MpsseTransport.open"), \
             patch("spc5tool.session.JtagTap") as tap, \
             patch("spc5tool.session.OnCE"), \
             patch("spc5tool.session.NexusMemory") as memory:
            tap.return_value.read_idcode.return_value = 0x0988101D
            memory.return_value.read32.return_value = 0xA0000003
            memory.return_value.read.return_value = PASSWORD
            with TargetSession(SPC5748G, password=PASSWORD, prepare_flash=False) as session:
                self.assertEqual(session.flash.authentication["method"], "stored_password")
            memory.return_value.read32.assert_called_once_with(0xFFFF4000)
            memory.return_value.write32.assert_not_called()

    def test_web_and_cli_report_matching_stored_password_and_keep_secrets_private(self):
        memory = Mock()
        memory.read32.return_value = 0xA0000003
        memory.read.return_value = PASSWORD
        flash = Flash(memory, SPC5748G)
        flash.authentication = login_result(memory, SPC5748G, PASSWORD)
        @contextlib.contextmanager
        def backend(*args):
            yield flash
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(directory)
            plan = workspace.plan(dict(target="spc5748g", action="authenticate", mode="hardware",
                                       password=PASSWORD.hex()))
            self.assertNotIn(PASSWORD.hex(), json.dumps(plan))
            with patch.object(workspace, "_backend", backend):
                job = workspace.start(plan["id"])
                deadline = time.monotonic() + 3
                while workspace.snapshot(job["id"])["status"] == "running" and time.monotonic() < deadline:
                    time.sleep(0.01)
            job = workspace.snapshot(job["id"])
            self.assertEqual(job["status"], "complete")
            self.assertEqual(job["result"]["authentication"]["method"], "stored_password")
            self.assertNotIn(plan["id"], workspace._plan_secrets)
            self.assertNotIn(PASSWORD.hex(), json.dumps(workspace.status()))
            path = Path(directory) / "password.txt"
            path.write_text(PASSWORD.hex())
            output = io.StringIO()
            with patch("spc5tool.cli.backend", backend), contextlib.redirect_stdout(output):
                self.assertEqual(main(["authenticate", "mpc5748g", "--password-file", str(path)]), 0)
            self.assertEqual(json.loads(output.getvalue())["authentication"]["method"], "stored_password")
            self.assertNotIn(PASSWORD.hex(), output.getvalue())

    def test_capabilities_and_installation_messages_match(self):
        capabilities = {c["key"]: c for c in catalog()}
        self.assertTrue(capabilities["spc5748g"]["password_login"])
        self.assertFalse(capabilities["spc58nh92"]["password_login"])
        self.assertIn("protection was not activated", installation_message(SPC5748G))
        self.assertIn("not yet supported", installation_message(SPC58NH92))


if __name__ == "__main__":
    unittest.main()
