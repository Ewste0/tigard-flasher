"""IEEE 1149.1 TAP state machine on top of the MPSSE transport.

Kept deliberately generic (variable IR/DR widths) because the e200 debug flow
needs 5-bit (MPC5607B JTAGC), 6-bit (MPC5748G JTAGC) and 10-bit (OnCE) IR scans,
plus wide DR scans (32-bit IDCODE, 65-bit CENSOR_CTRL, 256-bit JTAG password).
Owning the FSM here — rather than assuming a fixed IR length — is the reason the
tool is built from scratch on raw MPSSE instead of a fixed-IR JTAG helper.
"""

from __future__ import annotations
from collections import deque

# TAP states -> (next on TMS=0, next on TMS=1). Standard 1149.1 16-state FSM.
_T = {
    "RESET":      ("IDLE",       "RESET"),
    "IDLE":       ("IDLE",       "DRSELECT"),
    "DRSELECT":   ("DRCAPTURE",  "IRSELECT"),
    "DRCAPTURE":  ("DRSHIFT",    "DREXIT1"),
    "DRSHIFT":    ("DRSHIFT",    "DREXIT1"),
    "DREXIT1":    ("DRPAUSE",    "DRUPDATE"),
    "DRPAUSE":    ("DRPAUSE",    "DREXIT2"),
    "DREXIT2":    ("DRSHIFT",    "DRUPDATE"),
    "DRUPDATE":   ("IDLE",       "DRSELECT"),
    "IRSELECT":   ("IRCAPTURE",  "RESET"),
    "IRCAPTURE":  ("IRSHIFT",    "IREXIT1"),
    "IRSHIFT":    ("IRSHIFT",    "IREXIT1"),
    "IREXIT1":    ("IRPAUSE",    "IRUPDATE"),
    "IRPAUSE":    ("IRPAUSE",    "IREXIT2"),
    "IREXIT2":    ("IRSHIFT",    "IRUPDATE"),
    "IRUPDATE":   ("IDLE",       "DRSELECT"),
}


def _path(src: str, dst: str) -> list[int]:
    """Shortest list of TMS bits to walk from `src` to `dst` (BFS)."""
    if src == dst:
        return []
    seen = {src}
    q = deque([(src, [])])
    while q:
        state, bits = q.popleft()
        for tms in (0, 1):
            nxt = _T[state][tms]
            if nxt == dst:
                return bits + [tms]
            if nxt not in seen:
                seen.add(nxt)
                q.append((nxt, bits + [tms]))
    raise RuntimeError(f"no TAP path {src} -> {dst}")


class JtagTap:
    def __init__(self, transport):
        self.io = transport
        self.state = "RESET"

    # ---- navigation ----
    def reset(self):
        """Force Test-Logic-Reset (5x TMS=1 is always enough), then land in Idle."""
        self.io.clock_tms([1, 1, 1, 1, 1, 0])
        self.state = "IDLE"

    def goto(self, dst: str):
        if self.state == dst:
            return
        bits = _path(self.state, dst)
        self.io.clock_tms(bits)
        self.state = dst

    # ---- scans ----
    def scan_dr(self, nbits: int, value: int = 0, read: bool = True) -> int:
        """Shift `nbits` through the selected DR; return captured TDO. Ends in Idle."""
        self.goto("DRSHIFT")
        tdo = self.io.scan(nbits, value, read)   # last bit exits to DREXIT1
        self.state = "DREXIT1"
        self.goto("IDLE")
        return tdo

    def scan_ir(self, nbits: int, value: int, read: bool = False) -> int:
        """Load `nbits` into the IR; return captured IR (usually the status pattern).
        Ends in Idle."""
        self.goto("IRSHIFT")
        tdo = self.io.scan(nbits, value, read)   # last bit exits to IREXIT1
        self.state = "IREXIT1"
        self.goto("IDLE")
        return tdo

    def read_idcode(self) -> int:
        """Return the chip JTAGC ID, even after a previous auxiliary-TAP session.

        Test-Logic-Reset resets the selected TAP, not the shared-port mux.
        AN4365 section 1.3.2 specifies PAUSE-DR followed by UPDATE-DR to
        return ownership to JTAGC. First reset selects the current TAP's
        read-only identification register, so the handoff cannot update a
        previously selected debug command. No MCU reset pin is asserted.
        """
        self.reset()
        self.goto("DRPAUSE")
        self.goto("DRUPDATE")
        self.goto("IDLE")
        self.reset()
        return self.scan_dr(32, 0, read=True)
