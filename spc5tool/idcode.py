"""Read the JTAGC IDCODE and report IEEE manufacturer fields.

Known board IDs and limitations are recorded in docs/VALIDATION.md. A valid
IDCODE or manufacturer field alone does not establish an exact MCU profile.
"""

from __future__ import annotations
from dataclasses import dataclass

from .tap import JtagTap
from .chips import NXP_IDCODE_LOW12


@dataclass
class IdcodeResult:
    raw: int
    valid_ieee: bool          # bit0 == 1 (a real IDCODE, not BYPASS all-zeros)
    is_nxp: bool              # low 12 bits == 0x01D
    manufacturer: int         # bits [11:1]
    part_number: int          # bits [27:12]
    version: int              # bits [31:28]

    def __str__(self) -> str:
        if self.raw in (0x00000000, 0xFFFFFFFF):
            hint = ("all-zeros: BYPASS/no IDCODE, or TDO stuck low "
                    "(check wiring/VTGT; a censored 5748G reads dead)"
                    if self.raw == 0 else
                    "all-ones: TDO stuck high / not connected / target unpowered")
            return f"IDCODE = 0x{self.raw:08X}  ({hint})"
        tag = "NXP/Freescale" if self.is_nxp else "STMicroelectronics" if self.manufacturer == 0x020 else "UNKNOWN manufacturer"
        return (f"IDCODE = 0x{self.raw:08X}  [{tag}]\n"
                f"  version=0x{self.version:X}  part=0x{self.part_number:04X}  "
                f"mfr=0x{self.manufacturer:03X}  low12=0x{self.raw & 0xFFF:03X}")


def decode(raw: int) -> IdcodeResult:
    return IdcodeResult(
        raw=raw & 0xFFFFFFFF,
        valid_ieee=bool(raw & 1) and raw & 0xFFFFFFFF not in (0, 0xFFFFFFFF),
        is_nxp=(raw & 0xFFF) == NXP_IDCODE_LOW12,
        manufacturer=(raw >> 1) & 0x7FF,
        part_number=(raw >> 12) & 0xFFFF,
        version=(raw >> 28) & 0xF,
    )


def read_idcode(transport) -> IdcodeResult:
    tap = JtagTap(transport)
    return decode(tap.read_idcode())
