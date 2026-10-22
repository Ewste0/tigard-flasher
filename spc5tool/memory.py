"""Big-endian memory API with OnCE injection and Nexus implementations."""
import time
import math

from .errors import NexusBusError, OperationTimeout
from .geometry import checked_range
from .mpsse import MpsseBatch
from .once import OnCE
from .tap import JtagTap


class WordMemory:
    def read(self, address, size):
        checked_range(address, size)
        start = address & ~3
        data = bytearray()
        for addr in range(start, (address + size + 3) & ~3, 4):
            data.extend(self.read32(addr).to_bytes(4, "big"))
        return bytes(data[address - start:address - start + size])

    @staticmethod
    def word_address(address):
        checked_range(address, 4)
        if address & 3:
            raise ValueError("word access requires four-byte alignment")


class OnceMemory(WordMemory):
    """VLE-only e200z0h. Owns r0/r1 and first 128 SRAM bytes until reset."""
    def __init__(self, once):
        self.once = once

    def initialize(self):
        # e_stmw r0,0(r31): initializes ECC with 64-bit transfers (AN4365 4.2).
        self.once.step(0x181F0900, 0x40000000, force_operand=True)
        self.write32(0x40000000, 0x1800D000)  # valid VLE NOP fetch after each step
        self.write32(0x40000004, 0x1800D000)

    def read32(self, address):
        self.word_address(address)
        return self.once.step(0x501F0000, address, force_operand=True)  # e_lwz r0,0(r31)

    def write32(self, address, value):
        self.word_address(address)
        if not 0 <= value <= 0xFFFFFFFF:
            raise ValueError("word value out of range")
        self.once.step(0x1BE1D000, value, force_operand=True)  # e_ori r1,r31,0
        self.once.step(0x543F0000, address, force_operand=True)  # e_stw r1,0(r31)


class NexusMemory(WordMemory):
    """Physical accesses bypass the core MMU/cache; AN4365 3.2 and 3.4."""
    def __init__(self, once, timeout=2.0, *, fast_read=False, read_idle_clocks=32):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self.once, self.timeout = once, timeout
        if not isinstance(fast_read, bool):
            raise ValueError("fast_read must be a boolean")
        if not isinstance(read_idle_clocks, int) or not 1 <= read_idle_clocks <= 524288:
            raise ValueError("read idle clocks must be 1..524288")
        self.fast_read, self.read_idle_clocks = fast_read, read_idle_clocks
        self.fast_read_fallback = False

    def read(self, address, size):
        if not self.fast_read:
            return super().read(address, size)
        checked_range(address, size)
        if not any(r.name == "CFLASH" and r.base <= address and address + size <= r.base + r.size
                   for r in self.once.chip.regions):
            return super().read(address, size)
        start, end = address & ~3, (address + size + 3) & ~3
        data = bytearray()
        for base in range(start, end, 256):
            count = min(64, (end - base) // 4)
            if self.fast_read:
                chunk = self._read_batch(base, count)
            else:
                chunk = super().read(base, count * 4)
            data.extend(chunk)
        return bytes(data[address - start:address - start + size])

    def _read_batch(self, address, words):
        batch = MpsseBatch(self.once.tap.io)
        tap = JtagTap(batch)
        tap.state = self.once.tap.state
        once = OnCE(tap, self.once.chip)
        captures = []
        for i in range(words):
            once.nexus_write(9, address + 4 * i)
            once.nexus_write(7, 0x90000004 | self.once.chip.nexus_access)
            batch.clock_idle(self.read_idle_clocks)
            # Capture ready/error status before RWD, without a host round trip.
            # Reuse the established single-word protocol, not an unverified
            # multiword/burst command or speculative address increment.
            captures.append((once.nexus_read(7), once.nexus_read(10)))
        values = batch.execute()
        self.once.tap.state = tap.state
        pending = False
        for i, (status_index, _) in enumerate(captures):
            status = values[status_index]
            if status & 2:
                raise NexusBusError("read", address + 4 * i, status)
            if not status & 1:
                pending = True
        if pending:
            # Never accept stale RWD. Discard the whole batch and return to
            # polling for this session. End the last outstanding access before
            # starting fresh RWA/RWCS requests (AN4365 RWCS[AC]=0).
            self.fast_read, self.fast_read_fallback = False, True
            self.once.nexus_write(7, 0)
            return super().read(address, words * 4)
        return b"".join(values[data_index].to_bytes(4, "little") for _, data_index in captures)

    def _wait(self, read, address):
        deadline = time.monotonic() + self.timeout
        while True:
            status = self.once.nexus_read(7)
            if status & 2:
                raise NexusBusError("read" if read else "write", address, status)
            if bool(status & 1) == read:
                return
            if time.monotonic() >= deadline:
                raise OperationTimeout(f"Nexus {'read' if read else 'write'} timed out at 0x{address:08X}")
            time.sleep(0.001)

    def write_program_buffer(self, address, data):
        """Load one MPC5748G CFLASH quad-page, without asserting flash EHV.

        The caller owns PGM/lock setup and must check this result before EHV.
        No retries: an incomplete/failed queue must never commit its buffer.
        """
        if (self.once.chip.key != "spc5748g" or address % 128 or len(data) != 128 or
                not any(r.name == "CFLASH" and r.base <= address and address + 128 <= r.base + r.size
                        for r in self.once.chip.regions)):
            raise ValueError("queued programming requires one aligned MPC5748G CFLASH quad-page")
        batch = MpsseBatch(self.once.tap.io)
        tap = JtagTap(batch)
        tap.state = self.once.tap.state
        once = OnCE(tap, self.once.chip)
        captures = []
        for offset in range(0, 128, 4):
            once.nexus_write(9, address + offset)
            once.nexus_write(7, 0xD0000004 | self.once.chip.nexus_access)
            once.nexus_write(10, int.from_bytes(data[offset:offset + 4], "little"))
            batch.clock_idle(self.read_idle_clocks)
            captures.append(once.nexus_read(7))
        values = batch.execute()
        self.once.tap.state = tap.state
        for index, capture in enumerate(captures):
            status = values[capture]
            if status & 2:
                raise NexusBusError("write", address + index * 4, status)
            if status & 1:
                raise OperationTimeout(f"queued program buffer not ready at 0x{address + index * 4:08x}; buffer was not committed")

    def read32(self, address):
        self.word_address(address)
        self.once.nexus_write(9, address)
        self.once.nexus_write(7, 0x90000004 | self.once.chip.nexus_access)  # AC, SZ=32 bits, CNT=1
        self._wait(True, address)
        return int.from_bytes(self.once.nexus_read(10).to_bytes(4, "little"), "big")

    def write32(self, address, value):
        self.word_address(address)
        if not 0 <= value <= 0xFFFFFFFF:
            raise ValueError("word value out of range")
        self.once.nexus_write(9, address)
        self.once.nexus_write(7, 0xD0000004 | self.once.chip.nexus_access)
        # RWD must follow RWCS: writing RWD starts the bus transfer.
        self.once.nexus_write(10, int.from_bytes(value.to_bytes(4, "big"), "little"))
        self._wait(False, address)
