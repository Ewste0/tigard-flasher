"""A hardware-free FT2232H MPSSE backend for testing.

It implements the same (write_data, read_data_bytes) contract the real pyftdi
Ftdi object provides, so MpsseTransport runs unchanged against it. It *decodes*
the MPSSE command bytes the transport emits, drives an independent spec-built
1149.1 TAP + a simple JTAG device (IDCODE + IR), and returns TDO bytes.

Independence note: the TAP transition table and shift semantics here are written
straight from the IEEE 1149.1 spec, NOT imported from spc5tool.tap. So a passing
test means the transport's byte encoding, the tap FSM navigation, LSB-first bit
order, exit-on-last-bit, and read reassembly are all mutually consistent with the
standard — the strongest validation possible without silicon. (The one thing it
cannot catch is a wrong FTDI byte-convention vs real hardware; that's what the
first M0/M1 hardware run is for.)
"""

from __future__ import annotations

# 1149.1 FSM (spec) — independent copy.
_T = {
    "RESET": ("IDLE", "RESET"),
    "IDLE": ("IDLE", "DRSELECT"),
    "DRSELECT": ("DRCAPTURE", "IRSELECT"),
    "DRCAPTURE": ("DRSHIFT", "DREXIT1"),
    "DRSHIFT": ("DRSHIFT", "DREXIT1"),
    "DREXIT1": ("DRPAUSE", "DRUPDATE"),
    "DRPAUSE": ("DRPAUSE", "DREXIT2"),
    "DREXIT2": ("DRSHIFT", "DRUPDATE"),
    "DRUPDATE": ("IDLE", "DRSELECT"),
    "IRSELECT": ("IRCAPTURE", "RESET"),
    "IRCAPTURE": ("IRSHIFT", "IREXIT1"),
    "IRSHIFT": ("IRSHIFT", "IREXIT1"),
    "IREXIT1": ("IRPAUSE", "IRUPDATE"),
    "IRPAUSE": ("IRPAUSE", "IREXIT2"),
    "IREXIT2": ("IRSHIFT", "IRUPDATE"),
    "IRUPDATE": ("IDLE", "DRSELECT"),
}


class FakeTarget:
    """One JTAG device: after TLR the IDCODE register is selected on DR."""

    def __init__(self, idcode: int, ir_width: int = 5):
        self.idcode = idcode & 0xFFFFFFFF
        self.ir_width = ir_width
        self.state = "RESET"
        self.dr = 0
        self.dr_width = 32
        self.ir_shift = 0

    def _enter(self, nxt: str):
        if nxt == "DRCAPTURE":          # IR = IDCODE (reset default) -> load it
            self.dr = self.idcode
            self.dr_width = 32
        elif nxt == "IRCAPTURE":
            self.ir_shift = 0x1         # standard IR capture pattern (LSB=1)
        self.state = nxt

    def clock(self, tms: int, tdi: int) -> int:
        """One TCK. Returns the TDO bit sampled this clock, then transitions."""
        tdo = 0
        if self.state == "DRSHIFT":
            tdo = self.dr & 1
            self.dr = (self.dr >> 1) | ((tdi & 1) << (self.dr_width - 1))
        elif self.state == "IRSHIFT":
            tdo = self.ir_shift & 1
            self.ir_shift = (self.ir_shift >> 1) | ((tdi & 1) << (self.ir_width - 1))
        self._enter(_T[self.state][tms & 1])
        return tdo


class FakeMpsse:
    """Decodes the MPSSE byte stream and drives a FakeTarget."""

    def __init__(self, idcode: int, ir_width: int = 5):
        self.tgt = FakeTarget(idcode, ir_width)
        self._out = bytearray()

    # --- backend contract used by MpsseTransport ---
    def write_data(self, data: bytes) -> int:
        self._consume(memoryview(data))
        return len(data)

    def read_data_bytes(self, size: int) -> bytes:
        n = min(size, len(self._out))
        chunk = bytes(self._out[:n])
        del self._out[:n]
        return chunk

    # --- command decoder ---
    def _consume(self, mv: memoryview):
        i = 0
        n = len(mv)
        while i < n:
            op = mv[i]; i += 1
            if op in (0x8A, 0x8B, 0x97, 0x96, 0x8D, 0x8C, 0x85, 0x87):
                continue                                   # config / loopback / flush
            if op == 0x86:                                 # set clk divisor
                i += 2; continue
            if op in (0x8E, 0x8F):                          # idle clock, no data
                if op == 0x8E:
                    cycles = mv[i] + 1; i += 1
                else:
                    cycles = ((mv[i] | (mv[i + 1] << 8)) + 1) * 8; i += 2
                for _ in range(cycles):
                    self.tgt.clock(0, 0)
                continue
            if op in (0x80, 0x82):                          # set GPIO (val, dir)
                if op == 0x80 and hasattr(self.tgt, "reset_pin"):
                    self.tgt.reset_pin(not bool(mv[i] & 0x20))
                i += 2; continue
            if op in (0x19, 0x39):                          # bytes out (+in)
                length = mv[i] | (mv[i + 1] << 8); i += 2
                count = length + 1
                for _ in range(count):
                    self._shift_byte(mv[i], read=(op == 0x39)); i += 1
                continue
            if op in (0x1B, 0x3B):                          # bits out (+in)
                nbits = mv[i] + 1; i += 1
                self._shift_bits(mv[i], nbits, read=(op == 0x3B)); i += 1
                continue
            if op in (0x4B, 0x6B):                          # TMS out (+in)
                nbits = mv[i] + 1; i += 1
                self._tms(mv[i], nbits, read=(op == 0x6B)); i += 1
                continue
            raise AssertionError(f"unhandled MPSSE opcode 0x{op:02X} at offset {i-1}")

    def _shift_byte(self, byte: int, read: bool):
        val = 0
        for b in range(8):
            tdo = self.tgt.clock(0, (byte >> b) & 1)        # TMS=0 while shifting
            val |= tdo << b
        if read:
            self._out.append(val & 0xFF)                    # bytes: LSB-first

    def _shift_bits(self, byte: int, nbits: int, read: bool):
        val = 0
        for b in range(nbits):
            tdo = self.tgt.clock(0, (byte >> b) & 1)
            val |= tdo << b
        if read:
            self._out.append((val << (8 - nbits)) & 0xFF)   # sub-byte: left-justified

    def _tms(self, byte: int, nbits: int, read: bool):
        tdi = (byte >> 7) & 1                                # TDI held on bit7
        last = 0
        for b in range(nbits):
            last = self.tgt.clock((byte >> b) & 1, tdi)
        if read:
            self._out.append((last << 7) & 0xFF)            # TDO in bit7
