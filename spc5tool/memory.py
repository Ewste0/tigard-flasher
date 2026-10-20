"""Big-endian memory API with OnCE injection and Nexus implementations."""
import time
import math

from .errors import NexusBusError, OperationTimeout
from .geometry import checked_range


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
    def __init__(self, once, timeout=2.0):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self.once, self.timeout = once, timeout

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
