"""Standard SPI NOR driver for Winbond W25Q128JV (EF4018).

Tigard's SPI CS0 is BD3 on channel B. Protocol: Winbond W25Q128JV rev G.
Only the ordinary 24-bit array is supported; no status/OTP/security writes.
"""
import time
import math
from dataclasses import dataclass

from .errors import DriverError, OperationTimeout, VerificationError
from .geometry import checked_range
from .mpsse import DEFAULT_URL, DEFAULT_FREQUENCY
from .usb_backend import configure_usb, usb_error_message


@dataclass(frozen=True)
class SpiPart:
    key: str
    jedec: bytes
    size: int
    sector: int = 4096
    page: int = 256


PARTS = {"w25q128jv": SpiPart("w25q128jv", bytes.fromhex("ef4018"), 16 * 1024 * 1024)}


class SpiNor:
    def __init__(self, port, part, timeout=10.0):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        self.port, self.part, self.timeout = port, part, timeout
        self.controller = None

    @classmethod
    def open(cls, part, url=DEFAULT_URL, frequency=DEFAULT_FREQUENCY):
        if not 100 <= frequency <= 30_000_000:
            raise ValueError("SPI frequency must be between 100 Hz and 30 MHz")
        try:
            from pyftdi.spi import SpiController
        except ImportError as exc:
            raise DriverError("install pyftdi to access Tigard hardware") from exc
        configure_usb()
        controller = SpiController(cs_count=1)
        try:
            controller.configure(url)
            obj = cls(controller.get_port(cs=0, freq=frequency, mode=0), part)
            obj.controller = controller
            obj.identify()
            return obj
        except Exception as exc:
            controller.terminate()
            if isinstance(exc, DriverError):
                raise
            raise DriverError(f"cannot open Tigard SPI {url}: {usb_error_message(exc)}") from exc
        except BaseException:
            controller.terminate()
            raise

    def close(self):
        if self.controller:
            self.controller.terminate()
            self.controller = None

    def _exchange(self, command, count=0):
        try:
            result = bytes(self.port.exchange(bytes(command), count))
        except Exception as exc:
            raise DriverError(f"SPI transfer failed: {exc}") from exc
        if len(result) != count:
            raise DriverError("short SPI response")
        return result

    def identify(self):
        actual = self._exchange(b"\x9f", 3)
        if actual != self.part.jedec:
            raise DriverError(f"JEDEC mismatch: expected {self.part.jedec.hex()}, got {actual.hex()}")
        return actual

    def _range(self, address, size):
        checked_range(address, size)
        if address + size > self.part.size:
            raise ValueError("range exceeds SPI array")

    def _ready(self):
        deadline = time.monotonic() + self.timeout
        while self._exchange(b"\x05", 1)[0] & 1:
            if time.monotonic() >= deadline:
                raise OperationTimeout("SPI busy timeout; wait for completion before reconnecting")
            time.sleep(0.001)

    def _wren(self):
        self._ready()
        sr1 = self._exchange(b"\x05", 1)[0]
        sr2 = self._exchange(b"\x35", 1)[0]
        sr3 = self._exchange(b"\x15", 1)[0]
        if sr1 & 0x1C or sr2 & 0xC0 or sr3 & 4:
            raise DriverError("SPI protection or suspend is active; status registers are never changed automatically")
        self._exchange(b"\x06")
        if not self._exchange(b"\x05", 1)[0] & 2:
            raise DriverError("SPI write-enable latch did not set")

    def read(self, address, size):
        self._range(address, size)
        self._ready()
        if self._exchange(b"\x35", 1)[0] & 0x80:
            raise DriverError("SPI erase/program is suspended")
        result = bytearray()
        for offset in range(0, size, 4096):
            result.extend(self._exchange(b"\x03" + (address + offset).to_bytes(3, "big"), min(4096, size - offset)))
        return bytes(result)

    def verify(self, address, data):
        actual = self.read(address, len(data))
        if actual != data:
            offset = next(i for i, (a, b) in enumerate(zip(actual, data)) if a != b)
            raise VerificationError(f"SPI verify mismatch at 0x{address + offset:06x}")

    def erase(self, address, size):
        self._range(address, size)
        if address % self.part.sector or size % self.part.sector:
            raise ValueError("SPI erase requires whole 4096-byte sectors")
        for dest in range(address, address + size, self.part.sector):
            self._wren()
            self._exchange(b"\x20" + dest.to_bytes(3, "big"))
            self._ready()
            self.verify(dest, b"\xff" * self.part.sector)

    def program(self, address, data):
        self._range(address, len(data))
        if self.read(address, len(data)) != b"\xff" * len(data):
            raise DriverError("SPI programming requires erased bytes; use write to preserve/erase/program")
        offset = 0
        while offset < len(data):
            dest = address + offset
            size = min(self.part.page - dest % self.part.page, len(data) - offset)
            page = data[offset:offset + size]
            if page != b"\xff" * size:
                self._wren()
                self._exchange(b"\x02" + dest.to_bytes(3, "big") + page)
                self._ready()
            offset += size
        self.verify(address, data)

    def patch(self, address, data, backup):
        from .geometry import Block
        self._range(address, len(data))
        start = address // self.part.sector * self.part.sector
        end = (address + len(data) + self.part.sector - 1) // self.part.sector * self.part.sector
        originals = []
        for dest in range(start, end, self.part.sector):
            block = Block(f"SPI.{dest // self.part.sector}", "SPI", dest, self.part.sector, 0, 0, 0)
            originals.append((block, self.read(dest, block.size)))
        backup(originals)
        for block, original in originals:
            patched = bytearray(original)
            lo, hi = max(address, block.address), min(address + len(data), block.end)
            patched[lo - block.address:hi - block.address] = data[lo - address:hi - address]
            if patched != original:
                self.erase(block.address, block.size)
                self.program(block.address, bytes(patched))
        self.verify(address, data)
