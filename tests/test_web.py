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
from tests.backends import workspace_backend, tigard_descriptor
from tests.test_images import record


class WebTests(unittest.TestCase):
    def setUp(self):
        discovery = patch("spc5tool.web.discover_tigards", return_value=[tigard_descriptor()])
        self.discovery = discovery.start()
        self.addCleanup(discovery.stop)
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
        self.usb_open = self.usb.start()
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
        from spc5tool import __version__
        self.assertEqual(state["version"], __version__)
        self.assertTrue(state["hardware_enabled"])
        self.assertEqual(state["initial_mode"], "hardware")
        self.assertEqual(state["frequency"], 5000000)
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
        plan = workspace.plan(self.payload('spc5748g', address=0xFA8000))
        self.assertEqual(plan['frequency'], 100000)
        self.assertEqual(state["reset_pin"], "trst")
        self.assertEqual(state["jobs"], [])
        self.assertIsNone(state["active"])
        self.assertFalse((Path(self.temp.name) / "states").exists())

    def test_speed_write_and_erase_have_no_backup_or_verified_claim(self):
        data = b'\xA5' * 8 + b'\xff' * (0x8000 - 8)
        _, image = self.call('/api/files?name=speed.bin', data, raw=True)
        for action in ('write', 'erase'):
            payload = self.payload(action=action, length=0x8000, file_id=image['id'], speed_mode=True)
            status, plan = self.call('/api/plan', payload)
            self.assertEqual(status, 200, plan)
            self.assertTrue(plan['speed_mode'])
            self.assertFalse(plan['preserve'])
            self.assertEqual(plan['backup_bytes'], 0)
            self.assertEqual(plan['backup_ranges'], [])
            with patch('spc5tool.flash.Flash.read', side_effect=AssertionError('unexpected array read')):
                job = self.complete(payload)
            self.assertEqual(job['status'], 'complete', job)
            self.assertFalse(job['result']['verified'])
            self.assertFalse(job['result']['backup_created'])
            self.assertNotIn('backup', job['result'])
            self.assertIn('no backup', job['result']['message'])
        self.assertFalse(list(Path(self.temp.name).rglob('manifest.json')))
        standard = self.call('/api/plan', self.payload(action='write', file_id=image['id']))[1]
        self.assertFalse(standard['speed_mode'])
        self.assertTrue(standard['preserve'])
        self.assertEqual(standard['backup_bytes'], 0x8000)

    def test_speed_invalid_requests_fail_before_backend(self):
        _, image = self.call('/api/files?name=partial.bin', b'\xA5' * 8, raw=True)
        requests = [self.payload(action='write', file_id=image['id'], speed_mode=True)]
        requests += [self.payload(action='erase', length=0x8000, speed_mode=value) for value in (1, 'true', None)]
        requests += [self.payload(action='read', speed_mode=True),
                     self.payload('spc5748g', 'erase', address=0xFA8000, length=0x8000, speed_mode=True)]
        requests += [self.payload(action='erase', address=a, length=n, speed_mode=True)
                     for a, n in ((0x200000, 0x4000), (0x400000, 0x2000), (0xC00000, 0x2000))]
        with patch.object(self.server.workspace, '_backend') as backend:
            for payload in requests:
                self.assertEqual(self.call('/api/plan', payload)[0], 400, payload)
            backend.assert_not_called()

    def test_speed_stop_and_controller_failure_never_report_complete(self):
        from contextlib import contextmanager
        from spc5tool.chips import SPC560B64
        from spc5tool.flash import Flash
        from tests.test_speed_c90 import NoArrayReads
        from tests.test_batch_erase import lock_values
        _, image = self.call('/api/files?name=speed-stop.bin',
                             b'\xA5' * 16 + b'\xff' * (0x8000 - 16), raw=True)
        workspace = self.server.workspace
        for outcome in ('stop', 'failure'):
            memory = NoArrayReads(SPC560B64)
            locks = lock_values(memory)
            execute = memory._execute
            def interrupt(base, controller):
                execute(base, controller)
                if controller['mcr'] & 0x10:
                    if outcome == 'stop':
                        workspace.cancel_job(workspace.active)
                    else:
                        controller['mcr'] &= ~0x200
            @contextmanager
            def backend(plan, progress):
                with patch.object(memory, '_execute', interrupt):
                    yield Flash(memory, SPC560B64, progress=progress)
            with patch.object(workspace, '_backend', backend):
                job = self.complete(self.payload(action='write', file_id=image['id'], speed_mode=True))
            self.assertEqual(job['status'], 'cancelled' if outcome == 'stop' else 'failed', job)
            self.assertLess(job['progress'], 100)
            self.assertNotIn('backup', job['result'])
            self.assertNotEqual(job['result'].get('verified'), True)
            self.assertEqual(len(memory.pulses), 2)
            self.assertEqual(lock_values(memory), locks)
            self.assertFalse(memory.controllers[0xC3F88000]['mcr'] & 0x15)
            if outcome == 'stop':
                self.assertIn('No backup was made', job['result']['message'])

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

    def test_srecord_segments_roundtrip_original_download_and_address_guards(self):
        raw = record('1', 8, b'AAAAAAAA') + record('2', 0x800000, b'BBBBBBBB')
        code, image = self.call('/api/files?name=fixture.S19', raw, raw=True)
        self.assertEqual(code, 200, image)
        self.assertEqual(image['size'], 16)
        self.assertEqual(image['format'], 'srec')
        self.assertEqual(self.call('/api/download/' + image['id'])[1], raw)
        self.assertEqual(self.call('/api/buffer/' + image['id'] + '?segment=1')[1], b'BBBBBBBB')
        for kwargs in ({}, {'segment': 1, 'address': 0}, {'segment': True}, {'segment': 8}):
            code, error = self.call('/api/plan', self.payload(action='write', file_id=image['id'], **kwargs))
            self.assertEqual(code, 400, error)
        result = self.complete(self.payload(action='write', file_id=image['id'], segment=1, address=0x800000))
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(result['plan']['length'], 8)
        read = self.complete(self.payload(address=0x800000, length=16))
        self.assertEqual(self.call('/api/download/' + read['result']['file']['id'])[1], b'BBBBBBBB' + b'\xff' * 8)
        # The unselected segment remains untouched, even in a different region.
        read = self.complete(self.payload(address=0, length=32))
        self.assertEqual(self.call('/api/download/' + read['result']['file']['id'])[1], b'\xff' * 32)
        result = self.complete(self.payload(action='verify', file_id=image['id'], segment=1, address=0x800000))
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(self.call('/api/rename', {'file_id': image['id'], 'name': 'bad.bin'})[0], 400)
        self.assertEqual(self.call('/api/buffer/' + image['id'] + '?segment=2')[0], 400)
        self.assertEqual(self.call('/api/plan', self.payload(filename='read.S19'))[0], 400)
        _, binary = self.call('/api/files?name=raw.bin', b'\xff' * 8, raw=True)
        self.assertEqual(self.call('/api/rename', {'file_id': binary['id'], 'name': 'wrong.srec'})[0], 400)

    def test_srecord_otp_uses_program_and_rejects_bad_data_before_planning(self):
        raw = record('2', 0x400000, b'12345678')
        _, image = self.call('/api/files?name=otp.s19', raw, raw=True)
        payload = self.payload(action='program', address=0x400000, file_id=image['id'])
        self.assertEqual(self.call('/api/plan', {**payload, 'action': 'write'})[0], 400)
        result = self.complete(payload)
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(result['plan']['length'], 8)
        result = self.complete({**payload, 'action': 'verify'})
        self.assertEqual(result['status'], 'complete', result)
        files_before = len(self.server.workspace.files)
        self.assertEqual(self.call('/api/files?name=bad.s19', raw[:-4] + b'00\r\n', raw=True)[0], 400)
        self.assertEqual(len(self.server.workspace.files), files_before)

    def test_otp_full_workflow_and_unavailable_operations(self):
        for target, address, unit in (("spc560b64", 0x400000, 8), ("spc560b64", 0xC00000, 8),
                                      ("spc5748g", 0x401000, 8), ("spc58nh92", 0x401000, 16)):
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

    def test_fast_write_reports_phases_and_verified_result(self):
        _, image = self.call('/api/files?name=phase-test.bin', bytes(range(128)), raw=True)
        job = self.complete(self.payload('spc5748g', 'write', address=0xFA8000,
                                         file_id=image['id'], frequency=5000000))
        self.assertEqual(job['status'], 'complete', job)
        stats = job['result']['write_stats']
        self.assertEqual(stats['changed_blocks'], 1)
        self.assertEqual(stats['unchanged_blocks'], 0)
        for name in ('backup_seconds', 'erase_blank_seconds', 'program_seconds', 'verify_seconds'):
            self.assertGreaterEqual(stats[name], 0)
        self.assertIn('backup', job['result'])
        self.assertIn("finished", job)

    def test_grouped_erase_reports_separate_timings_and_preserves_backup(self):
        request = self.payload('spc5748g', 'erase', address=0xFA8000, length=0x8000)
        status, plan = self.call('/api/plan', request)
        self.assertEqual(status, 200, plan)
        self.assertEqual(plan['erase_batches'], 1)
        self.assertEqual(len(plan['blocks']), 2)
        job = self.complete(request)
        self.assertEqual(job['status'], 'complete', job)
        stats = job['result']['erase_stats']
        self.assertEqual((stats['blocks'], stats['batches']), (2, 1))
        for name in ('backup_seconds', 'erase_seconds', 'blank_check_seconds'):
            self.assertGreaterEqual(stats[name], 0)
        self.assertIn('backup', job['result'])

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

    def test_mutation_cancel_at_backup_or_after_backup_never_erases(self):
        from spc5tool.flash import Flash
        from tests.simulator import SimMemory
        from spc5tool.chips import SPC5748G
        from contextlib import contextmanager
        for action in ('erase', 'write', 'program'):
            for checkpoint in ('backup', 'before_modify'):
                with self.subTest(action=action, checkpoint=checkpoint):
                    entered, release = threading.Event(), threading.Event()
                    memory = SimMemory(SPC5748G)
                    workspace = self.server.workspace
                    @contextmanager
                    def backend(plan, progress):
                        flash = Flash(memory, SPC5748G, progress=progress)
                        if checkpoint == 'backup':
                            original_read = flash.read
                            def read(*args):
                                entered.set()
                                release.wait(5)
                                return original_read(*args)
                            flash.read = read
                        else:
                            original_unlock = flash._unlocked
                            @contextmanager
                            def unlocked(blocks):
                                entered.set()
                                release.wait(5)
                                with original_unlock(blocks):
                                    yield
                            flash._unlocked = unlocked
                        yield flash
                    image = workspace.add_file('cancel-test.bin', bytes(range(128)))
                    with patch.object(workspace, '_backend', backend):
                        plan = workspace.plan(self.payload('spc5748g', action=action, address=0xFA8000,
                                                           length=0x4000, file_id=image['id']))
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
                    self.assertEqual(final['status'], 'cancelled', final)
                    self.assertFalse(any(a == 0xFFFE0000 and v & 0x15 for a, v in memory.log))
                    self.assertEqual(memory.read(0xFA8000, 0x4000), b'\xff' * 0x4000)

    def test_cancel_after_mutation_waits_for_pulse_and_exports_backup(self):
        from spc5tool.flash import Flash
        entered, release = threading.Event(), threading.Event()
        original_wait = Flash._wait_done
        def waiting(flash, base, timeout=None):
            entered.set()
            release.wait(5)
            return original_wait(flash, base, timeout)
        workspace = self.server.workspace
        with patch.object(Flash, '_wait_done', waiting):
            plan = workspace.plan(self.payload('spc5748g', action='erase', address=0xFA8000, length=0x4000))
            job = workspace.start(plan['id'])
            self.assertTrue(entered.wait(2))
            try:
                current = workspace.snapshot(job['id'])
                self.assertTrue(current['cancellable'])
                self.assertTrue(current['modification_started'])
                self.assertGreaterEqual(current['elapsed_seconds'], 0)
                self.assertGreaterEqual(current['phase_seconds'], 0)
                self.assertEqual(self.call('/api/cancel', {'job_id': job['id']})[0], 200)
                self.assertEqual(workspace.snapshot(job['id'])['status'], 'running')
            finally:
                release.set()
            deadline = time.monotonic() + 5
            while workspace.active and time.monotonic() < deadline:
                time.sleep(0.01)
        final = workspace.snapshot(job['id'])
        self.assertEqual(final['status'], 'cancelled', final)
        self.assertTrue(final['result']['flash_may_be_modified'])
        self.assertTrue(final['result']['incomplete'])
        self.assertIn('partially erased or programmed', final['result']['message'])
        self.assertNotIn('Flash was not changed', final['result']['message'])
        self.assertLess(final['progress'], 100)
        self.assertFalse(final['cancellable'])
        _, backup = self.call('/api/download/' + final['result']['backup']['id'])
        with zipfile.ZipFile(io.BytesIO(backup)) as archive:
            self.assertIn('manifest.json', archive.namelist())

    def test_stop_programming_write_otp_and_shadow_has_truthful_result(self):
        from spc5tool.flash import Flash
        from spc5tool.security import validate_shadow
        for action, address in (('write', 0), ('program', 0x400000), ('write', 0x200000)):
            with self.subTest(action=action, address=hex(address)):
                workspace = self.server.workspace
                entered, release = threading.Event(), threading.Event()
                memories = []
                wait = Flash._wait_done
                def waiting(flash, base, timeout=None):
                    if flash.memory.controllers[base]['mcr'] & 0x10:
                        memories.append(flash.memory)
                        entered.set()
                        release.wait(5)
                    return wait(flash, base, timeout)
                image = workspace.add_file('stop-program.bin', bytes(range(16)))
                with patch.object(Flash, '_wait_done', waiting):
                    plan = workspace.plan(self.payload(action=action, address=address, file_id=image['id']))
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
                self.assertEqual(final['status'], 'cancelled', final)
                self.assertTrue(final['result']['incomplete'])
                self.assertTrue(final['result']['flash_may_be_modified'])
                self.assertIn('backup', final['result'])
                self.assertLess(final['progress'], 100)
                memory = memories[0]
                for controller in memory.controllers.values():
                    self.assertFalse(controller['mcr'] & 0x15)
                if action == 'program':
                    self.assertIn('OTP changes cannot be undone', final['result']['message'])
                    self.assertNotIn('Complete a Write', final['result']['message'])
                    self.assertEqual(memory.read(address, 16), bytes(range(8)) + b'\xff' * 8)
                elif address == 0x200000:
                    validate_shadow(memory.regions[address])
                    self.assertEqual(memory.read(address, 16), bytes(range(16)))
                else:
                    self.assertIn('including preserved bytes', final['result']['message'])
                    self.assertEqual(memory.read(address, 16), bytes(range(8)) + b'\xff' * 8)

    def test_adapter_detection_does_not_open_a_jtag_session(self):
        from types import SimpleNamespace, ModuleType
        from unittest.mock import Mock
        modules = {name: ModuleType(name) for name in ('pyftdi', 'pyftdi.ftdi', 'pyftdi.usbtools')}
        from spc5tool.adapters import discover_tigards
        from spc5tool.mpsse import DEFAULT_URL
        listing = Mock(return_value=[(SimpleNamespace(sn='TEST123', description='Tigard', bus=1, address=1), 2)])
        modules['pyftdi.ftdi'].Ftdi = SimpleNamespace(list_devices=listing)
        modules['pyftdi.usbtools'].UsbTools = Mock(BACKENDS=("usb.backend.libusb1", "usb.backend.libusb0"))
        with patch.dict('sys.modules', modules), patch('spc5tool.web.discover_tigards', discover_tigards):
            code, adapter = self.call('/api/adapter')
        self.assertEqual(code, 200)
        self.assertTrue(adapter['connected'])
        self.assertEqual(adapter['devices'][0]['serial'], 'TEST123')
        listing.assert_called_once_with(DEFAULT_URL)
        self.assertEqual(self.server.workspace.jobs, {})

    def test_adapter_replacement_invalidates_review_and_secret_before_run(self):
        workspace = self.server.workspace
        workspace.url = 'ftdi://ftdi:2232h:OLD/2'
        _, state = self.call('/api/adapter')
        self.assertEqual(state['selected_url'], tigard_descriptor()['url'])
        _, plan = self.call('/api/plan', self.payload(action='authenticate', password='123456789ABCDEF0'))
        self.assertIn(plan['id'], workspace._plan_secrets)
        self.discovery.return_value = [tigard_descriptor('NEW')]
        code, error = self.call('/api/run', {'plan_id': plan['id']})
        self.assertEqual(code, 400)
        self.assertIn('adapter changed', error['error'])
        self.assertFalse(workspace._plan_secrets)
        self.assertFalse(workspace.jobs)
        self.usb_open.assert_not_called()
        code, error = self.call('/api/plan', self.payload(adapter_revision=state['revision']))
        self.assertEqual(code, 400)
        self.assertIn('adapter changed', error['error'])

    def test_disconnection_and_usb_reenumeration_require_new_plan(self):
        workspace = self.server.workspace
        for devices in ([], [tigard_descriptor(address=2)]):
            self.discovery.return_value = [tigard_descriptor()]
            plan = workspace.plan(self.payload())
            self.discovery.return_value = devices
            with self.assertRaises(ValueError):
                workspace.start(plan['id'])
            self.assertFalse(workspace.jobs)
            self.assertNotIn(plan['id'], workspace.plans)
        self.discovery.side_effect = OSError('USB permission denied')
        state = workspace.adapter()
        self.assertFalse(state['connected'])
        self.assertIn('USB permission denied', state['message'])

    def test_multiple_tigards_explicit_selection_and_sticky_disconnect(self):
        first, second = tigard_descriptor('FIRST'), tigard_descriptor('SECOND', address=2)
        self.discovery.return_value = [first, second]
        _, state = self.call('/api/adapter')
        self.assertFalse(state['connected'])
        self.assertIn('Multiple', state['message'])
        self.assertEqual(self.call('/api/adapter', {'url':'ftdi://ftdi:2232h:UNKNOWN/2'})[0], 400)
        code, state = self.call('/api/adapter', {'url':second['url']})
        self.assertEqual(code, 200)
        self.assertEqual(state['selected_url'], second['url'])
        self.discovery.return_value = [first]
        _, state = self.call('/api/adapter')
        self.assertFalse(state['connected'])
        self.assertEqual(state['selection'], second['url'])
        _, state = self.call('/api/adapter', {'url':None})
        self.assertEqual(state['selected_url'], first['url'])
        # Plugging a second adapter in does not replace the current automatic choice.
        self.discovery.return_value = [second, first]
        self.assertEqual(self.call('/api/adapter')[1]['selected_url'], first['url'])

    def test_active_job_keeps_its_adapter_and_does_not_enumerate(self):
        workspace = self.server.workspace
        entered, release = threading.Event(), threading.Event()
        original = workspace._run
        def blocked(job):
            entered.set()
            release.wait(5)
            original(job)
        plan = workspace.plan(self.payload())
        with patch.object(workspace, '_run', blocked):
            job = workspace.start(plan['id'])
            self.assertTrue(entered.wait(2))
            try:
                self.discovery.reset_mock()
                self.discovery.return_value = [tigard_descriptor('OTHER')]
                self.assertEqual(workspace.adapter()['selected_url'], plan['adapter_url'])
                self.assertEqual(self.call('/api/adapter', {'url':None})[0], 400)
                self.discovery.assert_not_called()
            finally:
                release.set()
            deadline = time.monotonic() + 5
            while workspace.active and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertEqual(workspace.snapshot(job['id'])['plan']['adapter_url'], tigard_descriptor()['url'])


if __name__ == "__main__":
    unittest.main()
