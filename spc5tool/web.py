"""Loopback-only, dependency-free UI server for live Tigard hardware."""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import chips
from .adapters import discover_tigards
from .usb_backend import usb_error_message
from .errors import DriverError
from .files import save_backup, sha256, write_new
from .images import decode_image, MAX_IMAGE, MAX_UPLOAD, SREC_SUFFIXES
from .flash import Flash
from .geometry import Block, backup_blocks, blocks_for, select_blocks
from .idcode import read_idcode
from .mpsse import DEFAULT_URL, DEFAULT_FREQUENCY, MpsseTransport
from .session import TargetSession
from .spi import PARTS, SpiNor
from .security import parse_password, password_address, provision, validate_login, installation_message

MUTATING = {"write", "erase", "program", "install_password"}
ACTIONS = {"read", "write", "program", "verify", "erase", "blank", "identify", "install_password", "authenticate"}
ASSETS = Path(__file__).with_name("webui")
STATIC_ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/style.css": ("style.css", "text/css"),
                 "/app.js": ("app.js", "text/javascript"),
                 "/wiring.js": ("wiring.js", "text/javascript"),
                 "/assets/tigard-render.png": ("assets/tigard-render.png", "image/png"),
                 "/assets/spc560-10pin.svg": ("assets/spc560-10pin.svg", "image/svg+xml"),
                 "/assets/jtag-14pin.svg": ("assets/jtag-14pin.svg", "image/svg+xml")}


def number(value, name, maximum=0xFFFFFFFF):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, str):
        value = int(value, 0)
    if not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError(f"invalid {name}")
    return value


def catalog():
    targets = []
    for chip in chips.CHIPS.values():
        targets.append({"key": chip.key, "name": chip.part, "alias": chip.nxp_twin,
                        "protocol": "JTAG", "core": chip.core,
                        "read_only": chip.read_only, "read_presets": chip.read_presets,
                        "program_unit": chip.program_unit, "password_bits": chip.password_bits,
                        "password_install": chip.algorithm in ("c90-b", "c55-mpc5748", "c55-spc58h"),
                        "password_login": bool(chip.password_protocol),
                        "notes": chip.notes,
                        "regions": [{**asdict(r), "writable": all(b.writable for b in blocks_for(chip) if b.region == r.name)} for r in chip.regions],
                        "blocks": [asdict(b) for b in blocks_for(chip)]})
    for part in PARTS.values():
        targets.append({"key": part.key, "name": part.key.upper(), "alias": "Winbond SPI NOR",
                        "protocol": "SPI", "core": "JEDEC " + part.jedec.hex().upper(),
                        "regions": [{"name": "SPI", "base": 0, "size": part.size}],
                        "blocks": [], "sector": part.sector})
    return targets


def safe_filename(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("enter a filename")
    # Display/download name only: stored files always use generated UUIDs.
    if any(c in value for c in ('/', '\\', '\r', '\n')):
        raise ValueError("use a filename without folders or line breaks")
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", value.strip())[:100]
    if name in (".", ".."):
        raise ValueError("enter a filename")
    return name


class Workspace:
    def __init__(self, directory, allow_hardware=True, url=DEFAULT_URL, reset_pin="srst",
                 initial_mode="hardware", frequency=DEFAULT_FREQUENCY):
        if reset_pin not in ("srst", "trst"):
            raise ValueError("reset_pin must be srst or trst")
        if initial_mode != "hardware" or not allow_hardware:
            raise ValueError("this application supports live hardware only")
        number(frequency, "frequency", 30_000_000)
        if frequency < 1000:
            raise ValueError("frequency must be at least 1000 Hz")
        self.initial_mode, self.frequency = initial_mode, frequency
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        for folder in ("files", "backups"):
            (self.directory / folder).mkdir(exist_ok=True)
        self.allow_hardware, self.url = allow_hardware, url
        self.reset_pin = reset_pin
        self.files, self.plans, self.jobs = {}, {}, {}
        self._plan_secrets = {}
        self.lock = threading.RLock()
        self.active = None
        self.adapter_selection = None  # Automatic; an explicit UI choice stays pinned.
        self.adapter_revision = 0
        self._adapter_identity = None
        self.adapter_status = {"connected": False, "devices": [], "message": "Not checked",
                               "selected_url": None, "selection": None, "revision": 0}

    def adapter(self):
        if not self.allow_hardware:
            raise ValueError("hardware is disabled for this workspace")
        with self.lock:
            if self.active:
                return self.adapter_status
            try:
                devices = discover_tigards()
            except Exception as exc:
                return self._select_detected([], f"Tigard unavailable: {usb_error_message(exc)}")
            return self._select_detected(devices)

    def _select_detected(self, devices, error=None):
        available = [device for device in devices if device["url"]]
        preferred = self.adapter_selection or self.url
        selected = next((device for device in available if device["url"] == preferred), None)
        if not selected and self.adapter_selection is None and len(devices) == 1 and available:
            selected = available[0]
        identity = ((selected["url"], selected["bus"], selected["address"])
                    if selected else None)
        if identity != self._adapter_identity:
            self.adapter_revision += 1
            self._adapter_identity = identity
            self.plans.clear()
            self._plan_secrets.clear()
        if selected:
            self.url = selected["url"]
            message = f"USB detected · {selected['serial']}"
        elif error:
            message = error
        elif self.adapter_selection:
            message = "Selected Tigard is disconnected. Choose a connected adapter in Connection settings."
        elif not devices:
            message = "Tigard not found on USB. Connect an adapter; detection is automatic."
        elif not available:
            message = "Tigard serial is missing or duplicated; a unique USB serial is required."
        else:
            message = "Multiple Tigards detected. Choose an adapter in Connection settings."
        self.adapter_status = {"connected": selected is not None, "devices": devices,
                               "message": message, "selected_url": selected["url"] if selected else None,
                               "selection": self.adapter_selection, "revision": self.adapter_revision}
        return self.adapter_status

    def select_adapter(self, url):
        with self.lock:
            if self.active:
                raise ValueError("cannot change the adapter while an operation is running")
            state = self.adapter()
            if url not in (None, "") and (not isinstance(url, str) or
                    not any(device["url"] == url for device in state["devices"])):
                raise ValueError("choose a connected Tigard from the adapter list")
            self.adapter_selection = url or None
            return self._select_detected(state["devices"])

    def add_file(self, name, data, kind="image"):
        image = decode_image(name, data) if kind == "image" else None
        source = data
        if image:
            data = image.data
        limit = MAX_IMAGE + 1024 * 1024 if kind == "backup" else MAX_IMAGE
        if not data or len(data) > limit:
            raise ValueError("image must contain 1 byte to 16 MiB")
        key = uuid.uuid4().hex
        name = re.sub(r"[^A-Za-z0-9._ -]", "_", str(name))[:100] or "image.bin"
        path = self.directory / "files" / key
        write_new(path, data)
        info = {"id": key, "name": name, "size": len(data), "sha256": sha256(data), "kind": kind}
        if image and image.segments:
            write_new(path.with_suffix('.source'), source)
            info.update(format="srec", source_size=len(source), source_sha256=sha256(source),
                        entry_address=image.entry,
                        segments=[{"address": a, "offset": o, "size": s} for a, o, s in image.segments])
            if Path(name).suffix.lower() not in SREC_SUFFIXES:
                info['name'] = str(Path(name).with_suffix('.s19'))
        with self.lock:
            self.files[key] = info
        return dict(info)

    def file(self, key):
        with self.lock:
            if key not in self.files:
                raise ValueError("file is not in this session")
            return dict(self.files[key]), self.directory / "files" / key

    def rename_file(self, key, name):
        name = safe_filename(name)
        with self.lock:
            info, _ = self.file(key)
            if info.get("format") == "srec" and Path(name).suffix.lower() not in SREC_SUFFIXES:
                raise ValueError("S-record downloads preserve the original text; use a .s19 or .srec filename")
            if info.get("format") != "srec" and Path(name).suffix.lower() in SREC_SUFFIXES:
                raise ValueError("binary downloads cannot be renamed as S-record text; use .bin")
            self.files[key]["name"] = name
            for job in self.jobs.values():
                for item in (job["result"].get("file"), job["result"].get("backup")):
                    if item and item["id"] == key:
                        item["name"] = name
            return {**info, "name": name}

    def cancel_job(self, key):
        with self.lock:
            job = self.snapshot(key)
            if job["status"] != "running" or not job.get("cancellable"):
                raise ValueError("this operation cannot stop after flash modification has started")
            self.jobs[key]["cancel_requested"] = True
            return self.snapshot(key)

    def status(self):
        with self.lock:
            return {"targets": catalog(), "hardware_enabled": self.allow_hardware,
                    "url": self.url, "reset_pin": self.reset_pin, "workspace": str(self.directory),
                    "initial_mode": self.initial_mode, "frequency": self.frequency,
                    "active": self.active, "jobs": [self.snapshot(k) for k in list(self.jobs)[-20:]],
                    "files": list(self.files.values())}

    def plan(self, request):
        if not isinstance(request, dict):
            raise ValueError("expected an operation object")
        target, action, mode = (request.get(k) for k in ("target", "action", "mode"))
        if target not in chips.CHIPS and target not in PARTS:
            raise ValueError("unsupported target")
        if action not in ACTIONS or mode != "hardware":
            raise ValueError("unsupported operation or mode; live hardware only")
        if target in chips.CHIPS and chips.CHIPS[target].read_only and action in MUTATING:
            raise ValueError("Selected target is a read-only profile")
        frequency = number(request.get("frequency", self.frequency), "frequency", 30_000_000)
        fast_read = request.get("fast_read", False)
        if type(fast_read) is not bool:
            raise ValueError("fast_read must be a boolean")
        read_delay_us = number(request.get("read_delay_us", 10), "read delay", 1000)
        if read_delay_us < 1:
            raise ValueError("read delay must be 1..1000 microseconds")
        if fast_read and (target != "spc5748g" or action not in ("read", "write", "program", "erase", "verify", "blank")):
            raise ValueError("queued memory reads are available only for MPC5748G flash operations")
        if fast_read and frequency < 100000:
            raise ValueError("queued reads require a JTAG clock of at least 100 kHz")
        if frequency < 100:
            raise ValueError("frequency must be at least 100 Hz")
        expected = request.get("expected_id")
        expected = number(expected, "expected IDCODE") if expected not in (None, "") else None
        if mode == "hardware" and target in chips.CHIPS and action in MUTATING and expected is None:
            raise ValueError("identify the connected MCU and set its expected IDCODE before writing or erasing")
        address = number(request.get("address", 0), "address")
        size = number(request.get("length", 0), "length", MAX_IMAGE)
        file_id = request.get("file_id")
        info = None
        image_offset = 0
        segment_index = None
        secret = {}
        if request.get("password"):
            if target not in chips.CHIPS:
                raise ValueError("SPI targets do not support JTAG passwords")
            secret["login"] = parse_password(chips.CHIPS[target], request["password"])
            validate_login(chips.CHIPS[target], secret["login"])
        if action == "authenticate" and "login" not in secret:
            raise ValueError("Enter a password before opening a password-authenticated session")
        slot = request.get("password_slot", "jtag")
        if action == "install_password":
            if target not in chips.CHIPS or chips.CHIPS[target].algorithm not in ("c90-b", "c55-mpc5748", "c55-spc58h"):
                raise ValueError("Password provisioning for this family awaits its verified security register map")
            chip = chips.CHIPS[target]
            secret["new"] = parse_password(chip, request.get("new_password"), installing=True)
            if secret["new"] != parse_password(chip, request.get("confirm_password"), installing=True):
                raise ValueError("new passwords do not match")
            address, size = password_address(chip, slot), len(secret["new"])
        if action in ("write", "program", "verify"):
            info, _ = self.file(file_id)
            if info["kind"] == "backup":
                raise ValueError("a backup archive is not a raw firmware image")
            size = info["size"]
            if info.get("format") == "srec":
                segment_index = request.get("segment")
                if segment_index is None and len(info["segments"]) == 1:
                    segment_index = 0
                if type(segment_index) is not int or not 0 <= segment_index < len(info["segments"]):
                    raise ValueError("select an S-record segment explicitly")
                segment = info["segments"][segment_index]
                if address != segment["address"]:
                    raise ValueError(f"S-record segment requires address 0x{segment['address']:08X}; select its original range")
                size, image_offset = segment["size"], segment["offset"]
        blocks = ()
        if action not in ("identify", "authenticate"):
            if target in PARTS:
                part = PARTS[target]
                if size == 0 or address + size > part.size:
                    raise ValueError("range is empty or exceeds the SPI array")
                if action == "erase" and (address % part.sector or size % part.sector):
                    raise ValueError("erase requires whole 4096-byte sectors")
                start = address // part.sector * part.sector
                end = (address + size + part.sector - 1) // part.sector * part.sector
                blocks = tuple(Block(f"SPI.{a // part.sector}", "SPI", a, part.sector, 0, 0, 0)
                               for a in range(start, end, part.sector))
            else:
                blocks = select_blocks(chips.CHIPS[target], address, size, whole=action == "erase")
                if fast_read and any(b.region != "CFLASH" for b in blocks):
                    raise ValueError("queued reads are currently limited to MPC5748G CFLASH")
                if action in MUTATING:
                    Flash._check_blocks(blocks, erase=action == "erase")
                if action == "write" and any(b.kind == "otp" for b in blocks):
                    raise ValueError("OTP needs Program once; erase/preserve/write is not supported")
                if action == "program":
                    unit = chips.CHIPS[target].program_unit
                    if address % unit or size % unit:
                        raise ValueError(f"Program requires complete aligned {unit}-byte ECC units")
        if "fast_read" not in request:
            fast_read = (target == "spc5748g" and action in ("read", "write", "program", "erase", "verify", "blank") and frequency >= 100000
                         and bool(blocks) and all(b.region == "CFLASH" for b in blocks))
        backups = (backup_blocks(chips.CHIPS[target], address, size)
                   if target in chips.CHIPS and action in ("program", "install_password") else blocks)
        filename = safe_filename(request.get("filename") or f"{target}-{address:08x}-{mode}.bin")
        if action == "read" and Path(filename).suffix.lower() in SREC_SUFFIXES:
            raise ValueError("Read saves raw binary; use a .bin filename")
        plan = {"id": uuid.uuid4().hex, "target": target, "action": action, "mode": mode,
                "address": address, "length": size, "frequency": frequency, "expected_id": expected,
                "fast_read": fast_read, "read_delay_us": read_delay_us,
                "file_id": file_id if info else None, "image": info,
                "image_offset": image_offset, "segment": segment_index,
                "blocks": [asdict(b) for b in blocks], "backup_bytes": sum(b.size for b in backups),
                "backup_ranges": [asdict(b) for b in backups],
                "preserve": action == "write", "filename": filename}
        plan["password_slot"] = slot if action == "install_password" else None
        plan["notes"] = []
        if segment_index is not None:
            plan["notes"].append(f"S-record segment {segment_index + 1}/{len(info['segments'])}, using its original address. Only this segment is selected; gaps and other segments are not programmed.")
        if fast_read:
            plan["notes"].append(f"Queued CFLASH read; {read_delay_us} microsecond settling delay. Every word's status is checked; a target that is not ready falls back to standard reads.")
            if action in ("write", "program"):
                plan["notes"].append("MPC5748 CFLASH uses 128-byte programming for complete nonblank quad-pages. Buffer transfers are hardware checked; full programming timing awaits a write benchmark.")
        if any(b.kind == "otp" for b in blocks) and action in MUTATING:
            plan["notes"].append("One-time programming: only erased ECC units may be written. No erase or rollback is possible.")
            plan["notes"].append("Backup covers only the affected OTP units; unrelated factory ECC tests and password slots are not read.")
        if any(b.kind == "shadow" for b in blocks):
            plan["notes"].append("SHADOW erase preserves the configuration tail at +0x3DD0. Writing validates the censorship words before erasing.")
        if action == "install_password":
            plan["notes"].append("Installs the password only; existing censorship, lifecycle and DCF settings are preserved. This does not enable protection on an unprotected chip.")
        if "login" in secret and chips.CHIPS[target].password_protocol == "jtag256":
            plan["notes"].append("MPC5748G first attempts to halt under reset. Customer Delivery passwords are checked against the installed slot while halted. Protected-target password entry may run firmware briefly and awaits bench validation.")
        with self.lock:
            if self.active:
                raise ValueError("an operation is already running")
            adapter = self.adapter()
            if "adapter_revision" in request and request["adapter_revision"] != adapter["revision"]:
                raise ValueError("USB adapter changed; identify the target and review a new operation")
            plan["adapter_url"] = adapter["selected_url"]
            plan["adapter_revision"] = adapter["revision"]
            # Pending plans are small and disposable; one-use IDs prevent double submission.
            if len(self.plans) >= 100:
                expired = next(iter(self.plans))
                self.plans.pop(expired)
                self._plan_secrets.pop(expired, None)
            self.plans[plan["id"]] = plan
            if secret:
                self._plan_secrets[plan["id"]] = secret
        return plan

    def start(self, plan_id):
        with self.lock:
            if self.active:
                raise ValueError("an operation is already running")
            adapter = self.adapter()
            if not adapter["connected"]:
                raise ValueError(adapter["message"])
            if plan_id not in self.plans:
                raise ValueError("plan expired, adapter changed or already executed; review a new plan")
            plan = self.plans.pop(plan_id)
            key = uuid.uuid4().hex
            job = {"id": key, "plan": plan, "status": "running", "phase": "Opening session",
                   "progress": 0, "logs": [], "started": time.time(), "result": {}}
            job["cancellable"] = (plan["action"] in ("read", "verify", "blank") or
                                  plan["target"] in chips.CHIPS and plan["action"] in ("write", "erase", "program"))
            self.jobs[key] = job
            self.active = key
            threading.Thread(target=self._run, args=(job,), daemon=True).start()
            return self.snapshot(key)

    def snapshot(self, key):
        with self.lock:
            if key not in self.jobs:
                raise ValueError("unknown operation")
            return json.loads(json.dumps(self.jobs[key]))

    def _log(self, job, message):
        with self.lock:
            job["logs"].append({"time": time.time(), "message": message})

    @contextmanager
    def _backend(self, plan, progress):
        target = plan["target"]
        if target in PARTS:
            flash = SpiNor.open(PARTS[target], plan["adapter_url"], plan["frequency"])
            try:
                yield flash
            finally:
                flash.close()
        else:
            with TargetSession(chips.CHIPS[target], plan["adapter_url"], plan["frequency"], plan["expected_id"],
                               reset_pin=self.reset_pin, password=self._plan_secrets.get(plan["id"], {}).get("login"),
                               prepare_flash=plan["action"] != "authenticate",
                               fast_read=plan.get("fast_read", False), read_delay_us=plan.get("read_delay_us", 10)) as session:
                session.flash.progress = progress
                yield session.flash

    def _run(self, job):
        operation_started = time.monotonic()
        plan = job["plan"]
        action, target = plan["action"], plan["target"]
        backup_dir = self.directory / "backups" / job["id"]
        result = {}
        final_status = "failed"

        def progress(phase, done, total):
            with self.lock:
                if job.get("cancellable") and job.get("cancel_requested"):
                    raise ReadCancelled("Read stopped. No completed dump was saved." if action == "read" else
                                        "Operation stopped before flash modification. Flash was not changed.")
                job["phase"] = f"{action.capitalize()} · {phase}"
                # Only successful finalization may display 100%.
                fraction = round(100 * done / total) if total else 0
                job["progress"] = max(job["progress"], min(99, fraction))

        def backup(originals):
            save_backup(backup_dir, target, originals)
            self._log(job, f"Backup saved: {len(originals)} ranges with SHA-256 manifest.")
            with self.lock:
                job["result"]["backup_path"] = str(backup_dir)

        def before_modify():
            # Atomically close the cancellation window before any lock changes
            # or erase/program pulse. Never interrupt a partially rewritten block.
            with self.lock:
                if job.get("cancel_requested"):
                    raise ReadCancelled("Operation stopped before flash modification. Flash was not changed.")
                job["cancellable"] = False

        try:
            self._log(job, f"Opening Tigard channel B: {plan['adapter_url']}. MCU memory operations reset and halt the target.")
            if action == "identify":
                if target in PARTS:
                    with self._backend(plan, progress) as flash:
                        result = {"message": "JEDEC " + flash.identify().hex().upper()}
                else:
                    with MpsseTransport.open(plan["adapter_url"], plan["frequency"], reset_pin=self.reset_pin) as transport:
                        code = read_idcode(transport)
                        if not code.valid_ieee:
                            raise DriverError(str(code))
                        result = {"message": str(code), "idcode": code.raw}
            else:
                data = None
                if plan["file_id"]:
                    with self.file(plan["file_id"])[1].open('rb') as stream:
                        stream.seek(plan.get("image_offset", 0))
                        data = stream.read(plan["length"])
                    if len(data) != plan["length"]:
                        raise ValueError("image data is incomplete")
                self._log(job, f"{action.replace('_', ' ').capitalize()} {plan['length']:,} bytes at 0x{plan['address']:08X}.")
                with self._backend(plan, progress) as flash:
                    if isinstance(flash, Flash):
                        flash.before_modify = before_modify
                    states = getattr(flash, "debug_states", None)
                    if states:
                        result["debug_states"] = states
                        for core, state in states.items():
                            if state.get("prior_step_error"):
                                self._log(job, f"{core}: initial OSR={state['osr']} reported DEBUG with a prior instruction error. New debug instructions passed their status checks.")
                    if plan.get("fast_read"):
                        self._log(job, f"Queued CFLASH reads enabled for data, backups and verification; {plan['read_delay_us']} microseconds delay.")
                    authentication = getattr(flash, "authentication", None)
                    if authentication:
                        result["authentication"] = authentication
                        self._log(job, authentication["message"])
                    if action == "authenticate":
                        if not authentication:
                            raise DriverError("Backend did not provide a password-login result")
                        result["message"] = authentication["message"]
                    elif action == "read":
                        started = time.monotonic()
                        progress("read", 0, plan["length"])
                        data = flash.read(plan["address"], plan["length"])
                        elapsed = time.monotonic() - started
                        result["read_seconds"] = round(elapsed, 3)
                        result["bytes_per_second"] = round(len(data) / max(elapsed, 1e-9))
                        if plan.get("fast_read"):
                            result["fast_read_fallback"] = bool(flash.memory.fast_read_fallback)
                            if result["fast_read_fallback"]:
                                self._log(job, "Target was not ready after the queued delay. Discarded that batch and continued with polling reads.")
                        progress("read", plan["length"], plan["length"])
                        result["file"] = self.add_file(plan["filename"], data, "dump")
                        result["message"] = f"Read complete. SHA-256 {sha256(data)}"
                        self._log(job, f"Read {len(data):,} bytes in {elapsed:.2f} s ({result['bytes_per_second'] / 1024:.1f} KiB/s).")
                    elif action == "verify":
                        flash.verify(plan["address"], data)
                        result["message"] = "Verification passed. Every byte matches the loaded image."
                    elif action == "blank":
                        flash.verify(plan["address"], b"\xff" * plan["length"])
                        result["message"] = "Blank check passed. Every byte in the selected range is FF."
                    elif action == "write":
                        if isinstance(flash, Flash) and flash.bulk_program and plan.get("fast_read"):
                            self._log(job, "CFLASH Write: backup, erase with blank check, program, then one full-block verification. A separate Erase is unnecessary.")
                        flash.patch(plan["address"], data, backup)
                        if isinstance(flash, Flash) and flash.write_stats is not None:
                            result["write_stats"] = {k: round(v, 3) if isinstance(v, float) else v
                                                     for k, v in flash.write_stats.items()}
                            stats = result["write_stats"]
                            self._log(job, f"Write timing: backup {stats['backup_seconds']:.2f} s; "
                                      f"erase + blank check {stats['erase_blank_seconds']:.2f} s; "
                                      f"program {stats['program_seconds']:.2f} s; "
                                      f"verify {stats['verify_seconds']:.2f} s. "
                                      f"Blocks changed: {stats['changed_blocks']}; unchanged: {stats['unchanged_blocks']}.")
                        result["message"] = "Write verified. Bytes outside the image were preserved."
                    elif action == "program":
                        blocks = [Block(**b) for b in plan["backup_ranges"]]
                        originals = []
                        for index, b in enumerate(blocks):
                            if isinstance(flash, Flash):
                                with flash.stage("backup", 0.2 * index / len(blocks), 0.2 * (index + 1) / len(blocks)):
                                    originals.append((b, flash.read(b.address, b.size)))
                            else:
                                originals.append((b, flash.read(b.address, b.size)))
                        backup(originals)
                        if isinstance(flash, Flash):
                            with flash.stage("program and verify", 0.2, 1):
                                flash.program(plan["address"], data)
                        else:
                            flash.program(plan["address"], data)
                        result["message"] = "Program complete. Read-back verification passed."
                    elif action == "install_password":
                        provision(flash, self._plan_secrets[plan["id"]]["new"], backup, slot=plan["password_slot"])
                        result["message"] = installation_message(chips.CHIPS[target])
                    elif action == "erase":
                        blocks = [Block(**b) for b in plan["blocks"]]
                        originals = []
                        for index, b in enumerate(blocks):
                            if isinstance(flash, Flash):
                                with flash.stage("backup", 0.2 * index / len(blocks), 0.2 * (index + 1) / len(blocks)):
                                    originals.append((b, flash.read(b.address, b.size)))
                            else:
                                originals.append((b, flash.read(b.address, b.size)))
                                progress("backup", 0.2 * (index + 1) / len(blocks), 1)
                        backup(originals)
                        if isinstance(flash, Flash):
                            with flash.stage("erase", 0.2, 1):
                                flash.erase(plan["address"], plan["length"])
                        else:
                            progress("erase and blank check", 0.2, 1)
                            flash.erase(plan["address"], plan["length"])
                        result["message"] = ("SHADOW user area erased; security/boot configuration restored and verified."
                                             if any(b.kind == "shadow" for b in blocks) else "Erase complete. Blank check passed.")
            if plan.get("fast_read") and action != "read":
                result["fast_read_fallback"] = bool(flash.memory.fast_read_fallback)
                if result["fast_read_fallback"]:
                    self._log(job, "Target not ready after queued delay; remaining reads and program-buffer transfers used polling.")
            self._log(job, result["message"])
            self._log(job, f"{action.replace('_', ' ').capitalize()} finished in {time.monotonic() - operation_started:.2f} s.")
            final_status = "complete"
            with self.lock:
                job.update(phase="Finishing", progress=100)
        except ReadCancelled as exc:
            final_status = "cancelled"
            result["message"] = str(exc)
            self._log(job, str(exc))
        except Exception as exc:
            self._log(job, str(exc))
            with self.lock:
                job.update(phase="Failed", error=str(exc))
        finally:
            result["operation_seconds"] = round(time.monotonic() - operation_started, 3)
            self._plan_secrets.pop(plan["id"], None)
            if backup_dir.exists():
                try:
                    import io
                    stream = io.BytesIO()
                    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                        for path in sorted(backup_dir.iterdir()):
                            archive.write(path, path.name)
                    result["backup"] = self.add_file(f"backup-{target}-{job['id'][:8]}.zip", stream.getvalue(), "backup")
                except Exception as exc:
                    self._log(job, f"Backup ZIP export failed; original backup remains at {backup_dir}: {exc}")
            with self.lock:
                job["cancellable"] = False
                job["result"].update(result)
                job.update(status=final_status, phase=final_status.capitalize())
                job["finished"] = time.time()
                self.active = None


class ReadCancelled(Exception):
    pass


class Handler(BaseHTTPRequestHandler):
    server_version = "TigardUI/0.5"

    def log_message(self, *_):
        pass

    def _send(self, status, body, content_type="application/json", filename=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def _trusted(self):
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in hosts:
            raise PermissionError("invalid local host")
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + h for h in hosts}:
            raise PermissionError("cross-origin requests are not allowed")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise PermissionError("cross-site requests are not allowed")

    def do_GET(self):
        self._handle(False)

    def do_POST(self):
        self._handle(True)

    def _handle(self, post):
        try:
            self._trusted()
            url = urlsplit(self.path)
            path = url.path
            query = parse_qs(url.query)
            workspace = self.server.workspace
            if post:
                limit = MAX_UPLOAD if path == "/api/files" else 32768
                length = number(self.headers.get("Content-Length", "0"), "content length", limit)
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError("incomplete request")
                if path == "/api/files":
                    response = workspace.add_file(query.get("name", ["image.bin"])[0], body)
                else:
                    if self.headers.get_content_type() != "application/json":
                        raise PermissionError("JSON content type required")
                    request = json.loads(body or b"{}")
                    if not isinstance(request, dict):
                        raise ValueError("expected a JSON object")
                    if path == "/api/plan":
                        response = workspace.plan(request)
                    elif path == "/api/run":
                        response = workspace.start(request.get("plan_id"))
                    elif path == "/api/rename":
                        response = workspace.rename_file(request.get("file_id"), request.get("name"))
                    elif path == "/api/cancel":
                        response = workspace.cancel_job(request.get("job_id"))
                    elif path == "/api/adapter":
                        response = workspace.select_adapter(request.get("url"))
                    else:
                        self._send(404, {"error": "not found"})
                        return
                self._send(200, response)
            elif path == "/api/status":
                self._send(200, workspace.status())
            elif path == "/api/adapter":
                self._send(200, workspace.adapter())
            elif path.startswith("/api/jobs/"):
                self._send(200, workspace.snapshot(path.rsplit("/", 1)[1]))
            elif path.startswith(("/api/files/", "/api/download/", "/api/buffer/")):
                info, file_path = workspace.file(path.rsplit("/", 1)[1])
                if path.startswith("/api/download/"):
                    if info.get("format") == "srec":
                        file_path = file_path.with_suffix('.source')
                    self._send(200, file_path.read_bytes(), "application/octet-stream", info["name"])
                elif path.startswith("/api/buffer/"):
                    offset, size = 0, info["size"]
                    if info.get("format") == "srec":
                        index = number(query.get("segment", ["0"])[0], "segment", len(info["segments"]) - 1)
                        segment = info["segments"][index]
                        offset, size = segment["offset"], segment["size"]
                    with file_path.open('rb') as stream:
                        stream.seek(offset)
                        self._send(200, stream.read(size), "application/octet-stream")
                else:
                    offset = number(query.get("offset", ["0"])[0], "offset", info["size"])
                    with file_path.open("rb") as stream:
                        stream.seek(offset)
                        data = stream.read(256)
                    self._send(200, {**info, "offset": offset, "data": base64.b64encode(data).decode()})
            elif path in STATIC_ASSETS:
                name, mime = STATIC_ASSETS[path]
                self._send(200, (ASSETS / name).read_bytes(), mime)
            else:
                self._send(404, {"error": "not found"})
        except PermissionError as exc:
            self._send(403, {"error": str(exc)})
        except (ValueError, TypeError, KeyError, OSError, DriverError) as exc:
            self._send(400, {"error": str(exc)})


def make_server(directory, port=8765, allow_hardware=True, url=DEFAULT_URL, reset_pin="srst",
                initial_mode="hardware", frequency=DEFAULT_FREQUENCY):
    number(port, "port", 65535)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    try:
        server.workspace = Workspace(directory, allow_hardware, url, reset_pin, initial_mode, frequency)
    except Exception:
        server.server_close()
        raise
    return server


def serve(args):
    server = make_server(args.workspace, args.port, args.allow_hardware, args.url, args.reset_pin,
                         args.mode, args.freq)
    print(f"Tigard Flasher: http://127.0.0.1:{server.server_address[1]}", flush=True)
    print(f"Workspace: {server.workspace.directory}", flush=True)
    print(f"Initial mode: {args.mode}; clock: {args.freq:,} Hz" + "; live hardware only", flush=True)
    if args.open_browser:
        import webbrowser
        webbrowser.open(f"http://localhost:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # Let an in-progress erase/program complete and persist its state before exit.
        while server.workspace.active:
            time.sleep(0.1)
        server.server_close()
    return 0
