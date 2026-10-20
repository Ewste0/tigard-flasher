"""Exercise the UI's HTTP contract and real offline algorithms, without USB."""
import base64
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

from spc5tool.web import Workspace, make_server
from tests.backends import workspace_backend


class WebTests(unittest.TestCase):
    def setUp(self):
        backend = patch.object(Workspace, "_backend", workspace_backend)
        backend.start()
        self.addCleanup(backend.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.server = make_server(self.temp.name, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.usb = patch("spc5tool.web.MpsseTransport.open", side_effect=AssertionError("USB must not open"))
        self.spi = patch("spc5tool.web.SpiNor.open", side_effect=AssertionError("USB must not open"))
        self.usb.start()
        self.spi.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.usb.stop()
        self.spi.stop()
        self.temp.cleanup()

    def call(self, path, payload=None, raw=False, headers=None):
        data = payload if raw else json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.url + path, data=data,
                                     headers=headers or {"Content-Type": "application/octet-stream" if raw else "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                body = response.read()
                return response.status, json.loads(body) if response.headers.get_content_type() == "application/json" else body
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def complete(self, payload):
        status, plan = self.call("/api/plan", payload)
        self.assertEqual(status, 200, plan)
        status, job = self.call("/api/run", {"plan_id": plan["id"]})
        self.assertEqual(status, 200, job)
        deadline = time.monotonic() + 10
        while not job.get("finished") and time.monotonic() < deadline:
            time.sleep(0.01)
            _, job = self.call("/api/jobs/" + job["id"])
        self.assertIn("finished", job, job)
        return job

    def payload(self, target="spc560b64", action="read", **kwargs):
        return {"target": target, "action": action, "mode": "hardware", "expected_id": "0x0988101D", "address": 0,
                "length": 64, **kwargs}

    def test_static_assets_and_default_live_mode(self):
        for path in ("/", "/style.css", "/app.js", "/wiring.js", "/assets/tigard-render.png", "/assets/jtag-14pin.svg", "/assets/spc560-10pin.svg"):
            code, content = self.call(path)
            self.assertEqual(code, 200)
            self.assertGreater(len(content), 100)
        _, state = self.call("/api/status")
        self.assertTrue(state["hardware_enabled"])
        self.assertEqual(state["initial_mode"], "hardware")
        self.assertEqual(state["frequency"], 500000)
        self.assertEqual(state["reset_pin"], "srst")
        self.server.workspace.reset_pin = "trst"
        self.assertEqual(self.call("/api/status")[1]["reset_pin"], "trst")
        for path, mime, signature in (("/assets/tigard-render.png", "image/png", b"\x89PNG"),
                                      ("/assets/jtag-14pin.svg", "image/svg+xml", b"<svg")):
            with urllib.request.urlopen(self.url + path) as response:
                self.assertEqual(response.headers.get_content_type(), mime)
                self.assertTrue(response.read().startswith(signature))
        self.assertEqual(len(state["targets"]), 7)
        self.assertEqual(self.call("/../web.py")[0], 404)
        self.assertEqual(self.call("/assets/../../web.py")[0], 404)
        self.assertEqual(self.call("/api/demo", {})[0], 404)
        self.assertEqual(self.call("/api/plan", self.payload(mode="simulation"))[0], 400)

    def test_explicit_hardware_startup_does_not_connect(self):
        with self.assertRaisesRegex(ValueError, "live hardware only"):
            Workspace(self.temp.name, initial_mode="simulation")
        workspace = Workspace(self.temp.name, allow_hardware=True, reset_pin="trst",
                              initial_mode="hardware", frequency=100000)
        state = workspace.status()
        self.assertEqual(state["initial_mode"], "hardware")
        self.assertEqual(state["frequency"], 100000)
        self.assertEqual(state["reset_pin"], "trst")
        self.assertEqual(state["jobs"], [])
        self.assertIsNone(state["active"])
        self.assertFalse((Path(self.temp.name) / "states").exists())

    def test_all_targets_roundtrip_backup_verify_and_persistence(self):
        for target, address, blocksize in (("spc560b64", 0, 0x8000), ("spc5748g", 0xF90000, 0x4000), ("w25q128jv", 0, 4096)):
            with self.subTest(target=target):
                data = bytes(range(64))
                _, image = self.call("/api/files?name=fixture.bin", data, raw=True)
                written = self.complete(self.payload(target, "write", address=address + 8, file_id=image["id"]))
                self.assertEqual(written["status"], "complete", written)
                _, backup = self.call("/api/download/" + written["result"]["backup"]["id"])
                with zipfile.ZipFile(io.BytesIO(backup)) as archive:
                    manifest = json.loads(archive.read("manifest.json"))
                    self.assertEqual(manifest["target"], target)
                    self.assertEqual(manifest["blocks"][0]["size"], blocksize)
                    self.assertEqual(archive.read(manifest["blocks"][0]["file"]), b"\xff" * blocksize)
                read = self.complete(self.payload(target, address=address, length=80))
                _, actual = self.call("/api/download/" + read["result"]["file"]["id"])
                self.assertEqual(actual, b"\xff" * 8 + data + b"\xff" * 8)
                verified = self.complete(self.payload(target, "verify", address=address + 8, file_id=image["id"]))
                self.assertEqual(verified["status"], "complete")
                # Recreate the server-side workspace: persisted state still contains the patch.
                workspace = Workspace(self.temp.name)
                plan = workspace.plan(self.payload(target, address=address, length=80))
                with workspace._backend(plan, lambda *_: None) as flash:
                    self.assertEqual(flash.read(address, 80), actual)
                erased = self.complete(self.payload(target, "erase", address=address, length=blocksize))
                self.assertEqual(erased["status"], "complete")
                blank = self.complete(self.payload(target, "blank", address=address, length=blocksize))
                self.assertEqual(blank["status"], "complete")

    def test_verify_failure_is_visible_and_session_recovers(self):
        _, image = self.call("/api/files?name=wrong.bin", b"\x01" * 8, raw=True)
        failed = self.complete(self.payload(action="verify", file_id=image["id"]))
        self.assertEqual(failed["status"], "failed")
        self.assertIn("verify mismatch", failed["error"])
        self.assertEqual(self.complete(self.payload())["status"], "complete")

    def test_otp_full_workflow_and_unavailable_operations(self):
        for target, address, unit in (("spc560b64", 0x400000, 8), ("spc560b64", 0xC00000, 8),
                                      ("spc5748g", 0x401000, 8), ("spc58nh92", 0x400300, 16)):
            with self.subTest(target=target, address=address):
                data = bytes(range(unit))
                _, image = self.call("/api/files?name=otp-fixture.bin", data, raw=True)
                request = self.payload(target, "program", address=address, length=unit, file_id=image["id"])
                self.assertEqual(self.complete({**request, "action": "blank"})["status"], "complete")
                result = self.complete(request)
                self.assertEqual(result["status"], "complete", result)
                self.assertIn("backup", result["result"])
                self.assertEqual(self.complete({**request, "action": "verify"})["status"], "complete")
                result = self.complete({**request, "action": "read"})
                self.assertEqual(self.call("/api/download/" + result["result"]["file"]["id"])[1], data)
                failed = self.complete(request)
                self.assertEqual(failed["status"], "failed")
                self.assertLess(failed["progress"], 100)
                self.assertEqual(self.complete({**request, "action": "blank"})["status"], "failed")
                for action in ("write", "erase"):
                    self.assertEqual(self.call("/api/plan", {**request, "action": action})[0], 400)

    def test_password_jobs_discard_secrets_and_preserve_settings(self):
        for target, password in (("spc560b64", "123456789ABCDEF0"), ("spc58nh92", "01234567" * 8)):
            request = self.payload(target, "install_password", new_password=password, confirm_password=password)
            result = self.complete(request)
            self.assertEqual(result["status"], "complete", result)
            self.assertNotIn(password, json.dumps(result))
            self.assertEqual(self.server.workspace._plan_secrets, {})
        failed = self.complete(request)  # the same OTP slot cannot be programmed twice
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.server.workspace._plan_secrets, {})

    def test_backup_failure_never_modifies_simulated_flash(self):
        _, image = self.call("/api/files?name=fixture.bin", bytes(range(256)) * 2, raw=True)
        with patch("spc5tool.web.save_backup", side_effect=OSError("backup disk full")):
            failed = self.complete(self.payload(action="write", file_id=image["id"]))
        self.assertEqual(failed["status"], "failed")
        self.assertIn("backup disk full", failed["error"])
        read = self.complete(self.payload(length=512))
        _, actual = self.call("/api/download/" + read["result"]["file"]["id"])
        self.assertEqual(actual, b"\xff" * 512)

    def test_noop_patch_still_returns_a_backup_and_complete_result(self):
        _, image = self.call("/api/files?name=blank.bin", b"\xff" * 8, raw=True)
        job = self.complete(self.payload(action="write", file_id=image["id"]))
        self.assertEqual(job["status"], "complete")
        self.assertIn("backup", job["result"])
        self.assertIn("finished", job)

    def test_upload_preview_and_exact_download(self):
        data = bytes(range(256)) * 3
        _, image = self.call("/api/files?name=sample.bin", data, raw=True)
        _, preview = self.call(f"/api/files/{image['id']}?offset=512")
        self.assertEqual(base64.b64decode(preview["data"]), data[512:])
        self.assertEqual(self.call("/api/download/" + image["id"])[1], data)
        self.assertEqual(self.call(f"/api/files/{image['id']}?offset=-1")[0], 400)
        self.assertEqual(self.call("/api/files?name=empty.bin", b"", raw=True)[0], 400)

    def test_plan_does_not_modify_state_and_cannot_be_replayed(self):
        _, image = self.call("/api/files?name=fixture.bin", bytes(range(256)) * 2, raw=True)
        _, plan = self.call("/api/plan", self.payload(action="write", file_id=image["id"]))
        self.assertFalse((Path(self.temp.name) / "states").exists())
        self.assertEqual(list((Path(self.temp.name) / "backups").iterdir()), [])
        self.assertEqual(plan["length"], 512)
        self.assertTrue(plan["preserve"])
        # Consume without racing teardown against a live worker.
        workspace = self.server.workspace
        job = workspace.start(plan["id"])
        deadline = time.monotonic() + 5
        while workspace.active and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(workspace.snapshot(job["id"])["status"], "complete")
        self.assertEqual(self.call("/api/run", {"plan_id": plan["id"]})[0], 400)

    def test_security_and_range_rejections(self):
        for request in (self.payload(address=0x204000), self.payload(action="erase", length=8),
                        self.payload(length=0), self.payload(target="unknown"),
                        self.payload(address=True), self.payload(frequency=0),
                        self.payload(action="write", file_id="../../etc/passwd")):
            self.assertEqual(self.call("/api/plan", request)[0], 400, request)
        self.assertEqual(self.call("/api/status", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.call("/api/demo", {}, headers={"Content-Type": "application/json", "Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.call("/api/demo", {}, headers={"Content-Type": "text/plain"})[0], 403)
        self.assertEqual(self.call("/api/download/does-not-exist")[0], 400)

    def test_hardware_mutation_requires_idcode_before_transport(self):
        workspace = Workspace(self.temp.name, allow_hardware=True)
        for target in ("spc560b64", "spc5748g"):
            with self.assertRaisesRegex(ValueError, "expected IDCODE"):
                workspace.plan(self.payload(target, "erase", mode="hardware", expected_id=None, length=0x8000))

    def test_one_job_at_a_time(self):
        workspace = self.server.workspace
        entered, release = threading.Event(), threading.Event()
        original = workspace._run
        def blocked(job):
            entered.set()
            release.wait(5)
            original(job)
        with patch.object(workspace, "_run", blocked):
            first = workspace.plan(self.payload(action="identify"))
            second = workspace.plan(self.payload(action="identify"))
            workspace.start(first["id"])
            self.assertTrue(entered.wait(2))
            try:
                self.assertEqual(self.call("/api/run", {"plan_id": second["id"]})[0], 400)
            finally:
                release.set()
            deadline = time.monotonic() + 5
            while workspace.active and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNone(workspace.active)

    def test_custom_read_name_and_rename_download_headers(self):
        job = self.complete(self.payload('spc5748g', address=0xFA8000, length=0x8000, filename='My_Config.bin'))
        info = job['result']['file']
        self.assertEqual(info['name'], 'My_Config.bin')
        self.assertEqual(info['size'], 32768)
        code, renamed = self.call('/api/rename', {'file_id': info['id'], 'name': 'Car 12_Config.bin'})
        self.assertEqual(code, 200)
        self.assertEqual(renamed['sha256'], info['sha256'])
        with urllib.request.urlopen(self.url + '/api/download/' + info['id']) as response:
            self.assertEqual(response.headers.get_filename(), 'Car 12_Config.bin')
            self.assertEqual(response.read(), b'\xff' * 32768)
        for name in ('', '../outside.bin', 'folder/file.bin', 'bad\r\nHeader: value', '..'):
            self.assertEqual(self.call('/api/rename', {'file_id': info['id'], 'name': name})[0], 400)
        self.assertEqual(self.call('/api/jobs/' + job['id'])[1]['result']['file']['name'], 'Car 12_Config.bin')

    def test_stop_read_does_not_export_partial_dump(self):
        workspace = self.server.workspace
        entered, release = threading.Event(), threading.Event()
        original = workspace._run
        def blocked(job):
            entered.set()
            release.wait(5)
            original(job)
        with patch.object(workspace, '_run', blocked):
            plan = workspace.plan(self.payload('spc58nh92', address=0x800000, length=0x40000))
            job = workspace.start(plan['id'])
            self.assertTrue(entered.wait(2))
            try:
                self.assertEqual(self.call('/api/cancel', {'job_id': job['id']})[0], 200)
            finally:
                release.set()
            deadline = time.monotonic() + 5
            while workspace.active and time.monotonic() < deadline:
                time.sleep(0.01)
            final = workspace.snapshot(job['id'])
            self.assertEqual(final['status'], 'cancelled')
            self.assertNotIn('file', final['result'])
            self.assertEqual(workspace.files, {})
            self.assertEqual(self.call('/api/cancel', {'job_id': job['id']})[0], 400)

    def test_adapter_detection_does_not_open_a_jtag_session(self):
        from types import SimpleNamespace, ModuleType
        from unittest.mock import Mock
        modules = {name: ModuleType(name) for name in ('pyftdi', 'pyftdi.ftdi', 'pyftdi.usbtools')}
        listing = Mock(return_value=[(SimpleNamespace(sn='TEST123', description='Tigard'), 2)])
        modules['pyftdi.ftdi'].Ftdi = SimpleNamespace(list_devices=listing)
        modules['pyftdi.usbtools'].UsbTools = Mock()
        with patch.dict('sys.modules', modules):
            code, adapter = self.call('/api/adapter')
        self.assertEqual(code, 200)
        self.assertTrue(adapter['connected'])
        self.assertEqual(adapter['devices'][0]['serial'], 'TEST123')
        listing.assert_called_once_with(self.server.workspace.url)
        self.assertEqual(self.server.workspace.jobs, {})


if __name__ == "__main__":
    unittest.main()
