"""C90LC and C55 host-driven flash algorithms. No proprietary RAM blobs."""
import time
import math
from contextlib import contextmanager

from .errors import DriverError, NexusBusError, OperationTimeout, VerificationError
from .geometry import select_blocks, validate_program_range
from .mpsse import MpsseError

DONE, PEG, PGM, ERS, EHV = 0x400, 0x200, 0x10, 4, 1
ERRORS = 0xC000  # EER, RWE; W1C


class Flash:
    def __init__(self, memory, chip, timeout=10.0, progress=None):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        self.memory, self.chip, self.timeout = memory, chip, timeout
        self.progress = progress or (lambda phase, done, total: None)
        self.c55 = chip.algorithm.startswith("c55")
        self.poisoned = False
        self.before_modify = lambda: None
        self.bulk_program = False
        self.write_stats = None

    @contextmanager
    def stage(self, name, start=0, end=1):
        """Map nested work onto a stable fraction of the enclosing operation."""
        parent = self.progress
        def report(phase, done, total):
            fraction = min(1, max(0, done / total)) if total else 0
            parent(name if phase == "read" else f"{name} / {phase}",
                   round(start + (end - start) * fraction, 12), 1)
        self.progress = report
        report("read", 0, 1)
        try:
            yield
        finally:
            self.progress = parent

    def _idle(self, base):
        if self.poisoned:
            raise DriverError("session failed during flash cleanup; reconnect/reset before retrying")
        status = self.memory.read32(base)
        if not status & DONE or status & 0x1F:
            raise DriverError(f"flash controller is busy/suspended at 0x{base:08x}: MCR=0x{status:08x}")
        return status

    def read(self, address, size):
        blocks = select_blocks(self.chip, address, size)
        bases = {b.controller for b in blocks}
        for base in bases:
            self._idle(base)
            self.memory.write32(base, ERRORS)
        data = bytearray()
        for offset in range(0, size, 4096):
            count = min(4096, size - offset)
            try:
                chunk = self.memory.read(address + offset, count)
            except NexusBusError as exc:
                # RWCS alone cannot distinguish protection from an ECC fault.
                # A new Nexus access allows us to inspect the controller after
                # the failed transfer; retain the original error if this fails.
                block = next(b for b in blocks if b.address <= exc.address < b.end)
                try:
                    status = self.memory.read32(block.controller)
                except Exception:
                    raise exc
                detail = f"{exc}; flash MCR=0x{status:08X}."
                if (self.chip.algorithm == "c55-mpc5748" and
                        0x400060 <= exc.address < 0x4000A0 and status & (0x8000 | 0x10000000)):
                    detail += (" UTEST factory ECC test patterns deliberately trigger errors. "
                               "Select UTEST config + user (0x004000A0, length 0x3F60) "
                               "or UTEST user OTP (0x00401000, length 0x3000). "
                               "No complete dump was returned.")
                elif (self.chip.algorithm == "c55-spc58h" and
                        0x400280 <= exc.address < 0x400300 and status & 0x8000):
                    detail += (" SPC58 UTEST factory ECC test patterns deliberately trigger errors. "
                               "Select UTEST customer OTP (0x00401000, length 0x1000). "
                               "No complete dump was returned.")
                elif status & 0x8000:
                    detail += " The flash controller reports an ECC error."
                else:
                    detail += " Check the address and device read protection; RWCS alone does not identify the cause."
                raise DriverError(detail) from exc
            if len(chunk) != count:
                raise DriverError("short target memory read")
            data.extend(chunk)
            for base in bases:
                status = self.memory.read32(base)
                if status & (ERRORS | (0xF0000000 if self.c55 else 0)):
                    raise DriverError(f"flash read/ECC error near 0x{address + offset:08x}: MCR=0x{status:08x}")
            self.progress("read", len(data), size)
        return bytes(data)

    def verify(self, address, data):
        with self.stage("verify"):
            actual = self.read(address, len(data))
        if actual != data:
            offset = next(i for i, (a, b) in enumerate(zip(actual, data)) if a != b)
            raise VerificationError(f"verify mismatch at 0x{address + offset:08x}: "
                                    f"expected {data[offset]:02x}, read {actual[offset]:02x}")

    def _wait_done(self, base):
        deadline = time.monotonic() + self.timeout
        while True:
            status = self.memory.read32(base)
            if status & DONE:
                return status
            if time.monotonic() >= deadline:
                raise OperationTimeout(f"flash operation timed out at controller 0x{base:08x}")
            time.sleep(0.001)

    def _finish(self, base, mode):
        # Also aborts a timed-out operation. Never clear PGM/ERS until DONE.
        try:
            self.memory.write32(base, mode)
            self._wait_done(base)
            self.memory.write32(base, 0)
        except BaseException:
            self.poisoned = True
            raise

    @contextmanager
    def _operation(self, base, mode):
        self._idle(base)
        self.memory.write32(base, ERRORS)
        try:
            self.memory.write32(base, mode)
            yield
            self.memory.write32(base, mode | EHV)
            status = self._wait_done(base)
            if not status & PEG or status & (ERRORS | (0xF0000000 if self.c55 else 0)):
                raise DriverError(f"flash operation failed: MCR=0x{status:08x}")
        except MpsseError:
            # A failed USB exchange leaves the TAP/last write indeterminate.
            # Do not issue cleanup commands through that transport.
            self.poisoned = True
            raise
        finally:
            if not self.poisoned:
                self._finish(base, mode)

    @contextmanager
    def _unlocked(self, blocks):
        """Change only selected volatile lock bits; restore original locks."""
        self.before_modify()
        masks = {}
        for b in blocks:
            masks[(b.controller, b.bank)] = masks.get((b.controller, b.bank), 0) | b.mask
        saved = []
        try:
            for (base, bank), mask in masks.items():
                self._idle(base)
                entries = ((0x10 + bank * 4, None),) if self.c55 else (
                    ((4, 0xA1A11111), (12, 0xC3C33333)) if bank == 0 else ((8, 0xB2B22222),))
                for offset, key in entries:
                    address = base + offset
                    original = self.memory.read32(address)
                    saved.append((address, original, key))
                    if key is not None:
                        self.memory.write32(address, key)
                    self.memory.write32(address, original & ~mask)
                    if self.memory.read32(address) & mask:
                        raise DriverError(f"flash block remains locked at 0x{address:08x}; check protection/password state")
            yield
        except MpsseError:
            self.poisoned = True
            raise
        finally:
            if not self.poisoned:
                failure = None
                for address, original, key in reversed(saved):
                    try:
                        if key is not None:
                            self.memory.write32(address, key)
                        self.memory.write32(address, original)
                    except MpsseError as exc:
                        # TAP state and completion of this write are unknown.
                        # Stop immediately, including other saved lock banks.
                        self.poisoned = True
                        failure = exc
                        break
                    except Exception as exc:
                        # A reported controller error is distinct from a lost
                        # transport; attempt the remaining lock restorations.
                        failure = exc
                if failure is not None:
                    self.poisoned = True
                    raise DriverError(f"could not restore flash locks: {failure}") from failure

    def erase(self, address, size):
        blocks = select_blocks(self.chip, address, size, whole=True)
        self._check_blocks(blocks, erase=True)
        if any(b.kind == "shadow" for b in blocks):
            if len(blocks) != 1:
                raise ValueError("SHADOW must be handled separately")
            # Every new hardware session resets the target. Leaving this sector
            # blank would permanently censor it before the next Program command.
            with self.stage("preserve shadow configuration", 0, 0.1):
                original = self.read(address, size)
            from .security import validate_shadow
            validate_shadow(original)
            with self.stage("erase shadow user area", 0.1, 0.8):
                self._erase(address, size)
            with self.stage("restore shadow configuration", 0.8, 1):
                self.program(address + 0x3DD0, original[0x3DD0:])
            return
        self._erase(address, size)

    def _erase(self, address, size):
        self._require_writable()
        blocks = select_blocks(self.chip, address, size, whole=True)
        self._check_blocks(blocks, erase=True)
        with self._unlocked(blocks):
            for index, block in enumerate(blocks):
                self.progress("erase", round(index / len(blocks), 12), 1)
                base = block.controller
                with self._operation(base, ERS):
                    offsets = (0x38, 0x3C, 0x40, 0x44) if self.c55 else (
                        (0x10,) if block.region == "DFLASH" else (0x10, 0x14))
                    for bank, offset in enumerate(offsets):
                        # C90 SHADOW is selected by the interlock address.
                        # Its TSLK bit (20) belongs to LML/SLL, not LMS/HBS.
                        # RM30.6.3: LMS/HBS do not select the shadow sector.
                        value = block.mask if bank == block.bank and block.kind != "shadow" else 0
                        self.memory.write32(base + offset, value)
                        if self.memory.read32(base + offset) != value:
                            raise DriverError("flash erase selector did not latch")
                    self.memory.write32(block.address, 0xFFFFFFFF)
                # PEG alone does not prove a block was erased (e.g. a PASS lock).
                with self.stage("blank check", (index + 0.5) / len(blocks), (index + 1) / len(blocks)):
                    self.verify(block.address, b"\xff" * block.size)
                self.progress("erase", round((index + 1) / len(blocks), 12), 1)

    def program(self, address, data):
        validate_program_range(self.chip, address, len(data))
        self._program_checked(address, data)

    def _program_checked(self, address, data):
        """Program a validated customer range or a validated security slot.

        The only security caller is provision(), which checks the family,
        complete password length/value and exact slot before entering here.
        Alignment, blankness, locks and read-back checks remain mandatory.
        """
        self._require_writable()
        blocks = select_blocks(self.chip, address, len(data))
        self._check_blocks(blocks)
        unit = self.chip.program_unit
        if address % unit or len(data) % unit:
            raise ValueError(f"MCU programming requires complete, aligned {unit}-byte ECC units")
        for block in blocks:
            if block.kind == "shadow" and address + len(data) > block.address + 0x3DD0:
                from .security import validate_shadow
                with self.stage("validate shadow configuration", 0, 0):
                    proposed = bytearray(self.read(block.address, block.size))
                lo, hi = max(address, block.address), min(address + len(data), block.end)
                proposed[lo - block.address:hi - block.address] = data[lo - address:hi - address]
                validate_shadow(proposed)
        # Preflight the entire image before the first modification.
        with self.stage("blank preflight", 0, 0.1):
            existing = self.read(address, len(data))
        if existing != b"\xff" * len(data):
            raise DriverError("program requires erased flash; OTP units cannot be reused" if any(b.kind == "otp" for b in blocks)
                              else "program requires erased flash; use write for an erase/preserve/program cycle")
        with self.stage("program", 0.1, 0.9):
            self._program_erased(address, data, blocks)
        with self.stage("verify", 0.9, 1):
            self.verify(address, data)

    def _program_erased(self, address, data, blocks):
        """Internal: caller has checked alignment and blankness in this session.

        Only program()'s preflight or patch()'s completed erase/blank check may
        supply this proof. This helper does not replace final read-back verify.
        """
        unit = self.chip.program_unit
        with self._unlocked(blocks):
            block_index = 0
            offset = 0
            while offset < len(data):
                dest = address + offset
                while dest >= blocks[block_index].end:
                    block_index += 1
                block = blocks[block_index]
                count = unit
                # C55 quad-page programming: 128 aligned bytes per pulse.
                # Keep partial pages, all-ones ECC units and OTP on the original
                # single-unit path; never cross a block or quad-page boundary.
                if (self.bulk_program and self.chip.key == "spc5748g" and block.region == "CFLASH"
                        and dest % 128 == 0 and offset + 128 <= len(data) and dest + 128 <= block.end
                        and all(data[n:n + unit] != b"\xff" * unit for n in range(offset, offset + 128, unit))):
                    count = 128
                word = data[offset:offset + count]
                if word != b"\xff" * count:
                    with self._operation(blocks[block_index].controller, PGM):
                        if count == 128 and getattr(self.memory, "fast_read", False) and hasattr(self.memory, "write_program_buffer"):
                            try:
                                self.memory.write_program_buffer(dest, word)
                            except MpsseError:
                                # The TAP position is unknown after a short USB
                                # transfer. No EHV is queued by the buffer load;
                                # stop without issuing any more bus writes.
                                self.poisoned = True
                                raise
                        else:
                            for n in range(0, count, 4):
                                self.memory.write32(dest + n, int.from_bytes(word[n:n + 4], "big"))
                offset += count
                self.progress("program", offset, len(data))

    def patch(self, address, data, backup):
        """Back up every affected block BEFORE any erase; preserve its other bytes.

        backup receives [(Block, original_bytes), ...] and must persist them or
        raise. There is no rollback claim: a power loss can leave partial flash.
        """
        self._require_writable()
        blocks = select_blocks(self.chip, address, len(data))
        self._check_blocks(blocks)
        if any(b.kind == "otp" for b in blocks):
            raise ValueError("OTP requires Program once; erase/preserve/write is unavailable")
        if self.bulk_program and self.chip.key == "spc5748g" and all(b.region == "CFLASH" for b in blocks):
            return self._patch_cflash(address, data, blocks, backup)
        originals = []
        total = sum(b.size for b in blocks)
        done = 0
        for b in blocks:
            with self.stage("backup", 0.15 * done / total, 0.15 * (done + b.size) / total):
                originals.append((b, self.read(b.address, b.size)))
            done += b.size
        backup(originals)
        # Validate every rebuilt security sector before the first erase.
        for block, original in originals:
            if block.kind == "shadow":
                from .security import validate_shadow
                proposed = bytearray(original)
                lo, hi = max(address, block.address), min(address + len(data), block.end)
                proposed[lo - block.address:hi - block.address] = data[lo - address:hi - address]
                validate_shadow(proposed)
        for index, (block, original) in enumerate(originals):
            patched = bytearray(original)
            lo, hi = max(address, block.address), min(address + len(data), block.end)
            patched[lo - block.address:hi - block.address] = data[lo - address:hi - address]
            if patched != original:
                start = 0.15 + 0.8 * index / len(blocks)
                mid = start + 0.2 / len(blocks)
                end = 0.15 + 0.8 * (index + 1) / len(blocks)
                with self.stage("erase", start, mid):
                    self._erase(block.address, block.size)
                with self.stage("program", mid, end):
                    if block.kind == "shadow":
                        with self.stage("restore configuration", 0, 0.1):
                            self.program(block.address + 0x3DD0, bytes(patched[0x3DD0:]))
                        with self.stage("program user area", 0.1, 1):
                            self.program(block.address, bytes(patched[:0x3DD0]))
                    else:
                        self.program(block.address, bytes(patched))
            self.progress("write", round(0.15 + 0.8 * (index + 1) / len(blocks), 12), 1)
        with self.stage("final verification", 0.95, 1):
            self.verify(address, data)

    def _patch_cflash(self, address, data, blocks, backup):
        """Three passes: durable backup, erase/blank check, final full-block verify.

        Keep erasing changed blocks even if their backup reads FF: erased-looking
        data alone does not establish unused ECC. Never reuse proof from a prior
        connection, or use this path for OTP/security regions.
        """
        stats = self.write_stats = dict(backup_seconds=0, erase_blank_seconds=0,
                                       program_seconds=0, verify_seconds=0,
                                       changed_blocks=0, unchanged_blocks=0)
        originals, rebuilt = [], []
        total, done = sum(b.size for b in blocks), 0
        started = time.monotonic()
        for block in blocks:
            with self.stage("backup", 0.2 * done / total, 0.2 * (done + block.size) / total):
                original = self.read(block.address, block.size)
            originals.append((block, original))
            patched = bytearray(original)
            lo, hi = max(address, block.address), min(address + len(data), block.end)
            patched[lo - block.address:hi - block.address] = data[lo - address:hi - address]
            rebuilt.append(bytes(patched))
            done += block.size
        backup(originals)  # Must succeed before any volatile lock or flash change.
        stats["backup_seconds"] = time.monotonic() - started
        done = 0
        for (block, original), patched in zip(originals, rebuilt):
            start = 0.2 + 0.6 * done / total
            end = 0.2 + 0.6 * (done + block.size) / total
            if patched != original:
                stats["changed_blocks"] += 1
                mid = start + (end - start) / 3
                started = time.monotonic()
                with self.stage("erase and blank check", start, mid):
                    self._erase(block.address, block.size)
                stats["erase_blank_seconds"] += time.monotonic() - started
                # _erase verifies every byte is blank before returning. No other
                # target code runs between that proof and programming this block.
                started = time.monotonic()
                with self.stage("program", mid, end):
                    self._program_erased(block.address, patched, [block])
                stats["program_seconds"] += time.monotonic() - started
            else:
                stats["unchanged_blocks"] += 1
            self.progress("write", end, 1)
            done += block.size
        # Verify complete rebuilt blocks, including preserved bytes outside the
        # image and unchanged blocks. A single final pass covers all of them.
        done, started = 0, time.monotonic()
        for (block, _), patched in zip(originals, rebuilt):
            with self.stage("final verification", 0.8 + 0.2 * done / total,
                            0.8 + 0.2 * (done + block.size) / total):
                self.verify(block.address, patched)
            done += block.size
        stats["verify_seconds"] = time.monotonic() - started

    def _require_writable(self):
        if self.chip.read_only:
            raise DriverError(f"{self.chip.part} is a read-only profile")

    @staticmethod
    def _check_blocks(blocks, *, erase=False):
        for block in blocks:
            if not block.writable:
                raise ValueError(f"{block.region} is not writable")
            if erase and block.kind == "otp":
                raise ValueError(f"{block.region} is one-time programmable; silicon does not support erase")
