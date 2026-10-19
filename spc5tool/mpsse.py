"""FT2232H MPSSE transport for JTAG on a Tigard board (macOS, libusb/pyftdi).

This is the lowest layer of spc5tool. It knows nothing about e200/OnCE — it only
turns "shift these JTAG bits / move TMS like this" into FT2232H MPSSE command bytes
and back, over USB.

Transport parameters (Tigard pinout and FTDI AN_108):
  * Tigard JTAG is on FT2232H CHANNEL B (interface 1 / BDBUS), url ftdi://ftdi:2232h/2.
    NOT channel A (that is the Tigard UART). Building against ADBUS never sees the TAP.
  * Pin layout (from the shipped OpenOCD interface/ftdi/tigard.cfg, layout 0x0038/0x003b):
      BD0=TCK(out) BD1=TDI(out) BD2=TDO(in) BD3=TMS(out) BD4=nTRST(out) BD5=nSRST(out)
      direction=0x3b, idle value=0x38 (TMS=1, nTRST=1, nSRST=1, TCK=0, TDI=0).
    nTRST/nSRST are active-low.
  * MPSSE opcodes (FTDI AN_108), JTAG = LSB-first, TDI updated on falling TCK,
    TDO sampled on rising TCK:
      config: 0x8A disable /5 clk (->60MHz base), 0x97 disable adaptive, 0x8D disable 3-phase
      0x86 lo hi  set clk divisor; TCK = 60e6 / ((1+div)*2)
      0x80 val dir  set low-byte GPIO (BDBUS on channel B)
      0x87  send-immediate (flush read buffer)
      data shift (in Shift-DR/IR, holds TMS=0):
        0x39 bytes out+in   0x19 bytes out only
        0x3B bits  out+in   0x1B bits  out only
      TMS move (clocks TMS transitions, TDI held at bit7 of the data byte):
        0x6B TMS out+in     0x4B TMS out only

The transport is I/O-agnostic: it talks to any object exposing
    write_data(data: bytes) -> int
    read_data_bytes(size: int) -> bytes
Real hardware uses pyftdi's Ftdi (which provides exactly those). The test suite
swaps in a simulator implementing the same two methods, so the byte encoding below
is what actually gets validated.
"""

from __future__ import annotations
import math
from .errors import DriverError

# --- MPSSE opcodes ---
CLK_DIV5_DISABLE = 0x8A
ADAPTIVE_DISABLE = 0x97
THREE_PHASE_DISABLE = 0x8D
SET_CLK_DIVISOR = 0x86
SET_BITS_LOW = 0x80
SEND_IMMEDIATE = 0x87

# JTAG-flavoured data/TMS commands (LSB-first, -ve write edge, +ve read edge)
BYTES_OUT_IN = 0x39
BYTES_OUT = 0x19
BITS_OUT_IN = 0x3B
BITS_OUT = 0x1B
TMS_OUT_IN = 0x6B
TMS_OUT = 0x4B

# Tigard channel-B pin layout
DIR_LOW = 0x3B          # BD0/1/3/4/5 outputs, BD2 (TDO) input
IDLE_LOW = 0x38         # TMS=1, nTRST=1, nSRST=1, TCK=0, TDI=0
BIT_NTRST = 0x10        # BD4, active-low
BIT_NSRST = 0x20        # BD5, active-low

FTDI_MPSSE_CLOCK = 60_000_000  # 60 MHz base after disabling the /5 prescaler

DEFAULT_URL = "ftdi://ftdi:2232h/2"   # interface 2 == channel B


class MpsseError(DriverError):
    pass


class MpsseTransport:
    """MPSSE JTAG transport. Build via `MpsseTransport.open(...)` for real hardware,
    or `MpsseTransport(io)` to inject a custom byte-level backend (tests)."""

    def __init__(self, io, low_value: int = IDLE_LOW, *, reset_pin="srst"):
        if reset_pin not in ("srst", "trst"):
            raise ValueError("reset_pin must be srst or trst")
        self._io = io
        self._low = low_value & 0xFF
        self._closed = False
        self._srst_bit = BIT_NSRST if reset_pin == "srst" else BIT_NTRST
        self._trst_bit = BIT_NTRST if reset_pin == "srst" else BIT_NSRST

    # ---- construction ----
    @classmethod
    def open(cls, url: str = DEFAULT_URL, freq: float = 1_000_000.0, *, reset_pin="srst"):
        """Open the Tigard channel-B MPSSE engine on real hardware via pyftdi."""
        if not math.isfinite(freq) or not 100 <= freq <= 30_000_000:
            raise ValueError("frequency must be between 100 Hz and 30 MHz")
        if reset_pin not in ("srst", "trst"):
            raise ValueError("reset_pin must be srst or trst")
        try:
            from pyftdi.ftdi import Ftdi
            from pyftdi.usbtools import UsbTools
        except ImportError as exc:  # pragma: no cover - hardware path
            raise MpsseError(
                "pyftdi not installed. Install with:\n"
                "  python3 -m pip install pyftdi\n"
                "(pure-Python over libusb; kext-less on macOS 12+/Apple Silicon)."
            ) from exc
        # Re-enumerate between sessions: a GUI can outlive a USB reconnect or
        # another process opening the adapter. Do not reuse stale descriptors.
        UsbTools.flush_cache()
        ftdi = Ftdi()
        # open_mpsse_from_url sends 0x8A/0x8D/0x97, sets frequency, and applies the
        # low-byte direction/initial we pass (BDBUS on interface 2).
        try:
            ftdi.open_mpsse_from_url(
                url, direction=DIR_LOW, initial=IDLE_LOW, frequency=freq
            )
        except Exception as exc:
            ftdi.close()
            raise MpsseError(f"cannot open Tigard {url}: {exc}") from exc
        return cls(_PyftdiIO(ftdi), low_value=IDLE_LOW, reset_pin=reset_pin)

    def close(self):
        if not self._closed:
            self._closed = True
            close = getattr(self._io, "close", None)
            if close:
                close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _write(self, data):
        if self._closed:
            raise MpsseError("transport is closed")
        if self._io.write_data(data) != len(data):
            raise MpsseError("short MPSSE write; TAP state is unknown, reconnect")

    # ---- low-byte GPIO (reset lines) ----
    def set_reset(self, *, trst: bool | None = None, srst: bool | None = None):
        """Assert/release logical reset lines, using the configured pin mapping.

        With reset_pin='trst', logical SRST is physical BD4 and logical
        TRST/JCOMP is physical BD5. Both are initially high.
        """
        if trst is not None:
            self._low = (self._low & ~self._trst_bit) if trst else (self._low | self._trst_bit)
        if srst is not None:
            self._low = (self._low & ~self._srst_bit) if srst else (self._low | self._srst_bit)
        self._write(bytes([SET_BITS_LOW, self._low, DIR_LOW]))

    # ---- clocking primitives ----
    def clock_tms(self, tms_bits, tdi: int = 0):
        """Clock a sequence of TMS transitions (list of 0/1), TDI held at `tdi`.
        No TDO is read. Used purely to walk the TAP state machine."""
        if any(b not in (0, 1) for b in tms_bits) or tdi not in (0, 1):
            raise ValueError("TMS and TDI must be bits")
        cmd = bytearray()
        i = 0
        n = len(tms_bits)
        tdi_hi = 0x80 if tdi else 0x00
        while i < n:
            chunk = tms_bits[i:i + 7]           # <=7 TMS clocks per command
            byte = tdi_hi
            for b, val in enumerate(chunk):
                if val:
                    byte |= (1 << b)
            cmd += bytes([TMS_OUT, len(chunk) - 1, byte])
            i += len(chunk)
        if cmd:
            self._write(bytes(cmd))
            self._low = (self._low & ~0x0B) | (tms_bits[-1] << 3) | (tdi << 1)

    def scan(self, nbits: int, tdi: int = 0, read: bool = True) -> int:
        """Shift `nbits` (LSB-first) while in Shift-DR/Shift-IR.

        The final bit is clocked together with TMS=1, so on return the TAP has
        advanced to Exit1-DR/Exit1-IR. Returns the TDO value (LSB-first) if `read`.

        Splits into: full bytes (0x39/0x19) + remainder bits (0x3B/0x1B) for the
        first nbits-1 bits, then the last bit via a TMS command (0x6B/0x4B) that
        drives TMS=1 while carrying the last TDI bit in bit7.
        """
        if not 1 <= nbits <= 524288 or tdi < 0 or tdi.bit_length() > nbits:
            raise ValueError("scan width must be 1..524288 and value must fit")
        cmd = bytearray()
        expected = 0
        head = nbits - 1                       # bits before the TMS-exit bit
        full = head // 8
        rem = head % 8

        # full bytes
        if full:
            length = full - 1
            cmd += bytes([BYTES_OUT_IN if read else BYTES_OUT, length & 0xFF, length >> 8])
            cmd += (tdi & ((1 << (full * 8)) - 1)).to_bytes(full, "little")
            expected += full if read else 0
        # remainder bits (< 8)
        if rem:
            byte = (tdi >> (8 * full)) & ((1 << rem) - 1)
            if read:
                cmd += bytes([BITS_OUT_IN, rem - 1, byte]); expected += 1
            else:
                cmd += bytes([BITS_OUT, rem - 1, byte])
        # last bit, clocked with TMS=1 -> Exit1
        last = (tdi >> (nbits - 1)) & 1
        tms_byte = (last << 7) | 0x01           # TMS=1 on bit0, TDI level on bit7
        if read:
            cmd += bytes([TMS_OUT_IN, 0x00, tms_byte]); expected += 1
        else:
            cmd += bytes([TMS_OUT, 0x00, tms_byte])

        if read:
            cmd += bytes([SEND_IMMEDIATE])
        self._write(bytes(cmd))
        self._low = (self._low & ~0x0B) | 0x08 | (last << 1)
        if not read:
            return 0

        data = self._read_exact(expected)
        return self._assemble(data, full, rem, nbits)

    # ---- helpers ----
    def _read_exact(self, n: int) -> bytes:
        buf = bytearray()
        # a couple of attempts in case the USB read returns short
        for _ in range(8):
            if len(buf) >= n:
                break
            chunk = self._io.read_data_bytes(n - len(buf))
            if chunk:
                buf += chunk
        if len(buf) < n:
            raise MpsseError(f"short MPSSE read: got {len(buf)} of {n} bytes")
        return bytes(buf[:n])

    @staticmethod
    def _assemble(data: bytes, full: int, rem: int, nbits: int) -> int:
        """Rebuild the LSB-first TDO integer from the returned MPSSE bytes.
        Byte reads come back LSB-first (bit0 = first clocked). Sub-byte bit reads
        come back left-justified in the MSBs (>> (8-rem) to right-justify). The
        TMS-command bit read returns the single TDO bit in bit7."""
        val = 0
        pos = 0
        idx = 0
        for _ in range(full):
            val |= data[idx] << pos
            pos += 8
            idx += 1
        if rem:
            val |= (data[idx] >> (8 - rem)) << pos
            pos += rem
            idx += 1
        last_bit = (data[idx] >> 7) & 1          # from the 0x6B TMS command
        val |= last_bit << (nbits - 1)
        return val


class _PyftdiIO:
    """Adapts pyftdi's Ftdi to the (write_data, read_data_bytes) backend contract."""

    def __init__(self, ftdi):
        self._ftdi = ftdi

    def write_data(self, data: bytes) -> int:  # pragma: no cover - hardware path
        try:
            return self._ftdi.write_data(data)
        except Exception as exc:
            raise MpsseError(f"USB write failed: {exc}") from exc

    def read_data_bytes(self, size: int) -> bytes:  # pragma: no cover - hardware
        try:
            return self._ftdi.read_data_bytes(size, attempt=4)
        except Exception as exc:
            raise MpsseError(f"USB read failed: {exc}") from exc

    def close(self):
        self._ftdi.close()
