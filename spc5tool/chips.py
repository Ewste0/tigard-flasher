"""Device descriptors. Executable erase geometry lives in geometry.py.

Addresses are based on the local manuals/device header. MPC5748G sample and
configuration reads have been exercised; hardware erase/programming and other
MCU profiles remain unvalidated. Legacy unlock metadata is descriptive;
the driver does not implement password authentication or security provisioning.
"""

from __future__ import annotations
from dataclasses import dataclass, field

# JEDEC bank-0 manufacturer id for NXP/Freescale is 0x00E, so the low 12 bits of a
# valid 1149.1 IDCODE (manufacturer<<1 | 1) are 0x01D.
NXP_IDCODE_LOW12 = 0x01D


@dataclass(frozen=True)
class FlashRegion:
    name: str
    base: int
    size: int
    note: str = ""


@dataclass(frozen=True)
class Chip:
    key: str
    part: str
    nxp_twin: str
    family: str
    core: str
    jtagc_irlen: int          # JTAGC instruction-register width
    once_irlen: int           # OnCE command-register width
    has_jcomp: bool           # 5748G needs JCOMP high; 5607B has no JCOMP pin
    has_mmu: bool
    sram_base: int
    flash_ctrl: dict          # controller register bases
    regions: tuple            # FlashRegion...
    unlock: dict = field(default_factory=dict)
    notes: tuple = ()
    read_only: bool = False
    read_presets: tuple = ()
    core_selectors: tuple = ()
    nexus_access: int = 0
    idcodes: tuple = ()


SPC560B64 = Chip(
    key="spc560b64",
    part="SPC560B64L3",
    nxp_twin="MPC5607B",
    family="Bolero (MPC560xB)",
    core="e200z0h (VLE, no MMU)",
    jtagc_irlen=5,
    once_irlen=10,
    has_jcomp=False,
    has_mmu=False,
    sram_base=0x40000000,
    flash_ctrl={"CFLASH": 0xC3F88000, "DFLASH": 0xC3F8C000, "SWT": 0xFFF38000},
    regions=(
        FlashRegion("CFLASH", 0x00000000, 0x00180000,
                    "Low0-5:1x32k,2x16k,2x32k,1x128k; Mid0-1:2x128k; High0-7:8x128k"),
        FlashRegion("DFLASH", 0x00800000, 0x00010000, "Low0-3: 4x16k"),
        FlashRegion("SHADOW", 0x00200000, 0x00004000,
                    "*** NEVER erase blank: self-censors, locks JTAG ***"),
    ),
    unlock={
        # host writes these to the CFLASH controller before erase/program
        "LML_KEY": 0xA1A11111, "HBL_KEY": 0xB2B22222, "SLL_KEY": 0xC3C33333,
        # 64-bit reset-time censorship (default public), swap hi/lo dword for C90LC
        "CENSOR_DEFAULT": 0xFEEDFACECAFEBEEF,
    },
    notes=(
        "No JCOMP pin: JTAGC always enabled.",
        "Single core: ACCESS_AUX_TAP_ONCE unambiguously targets the one core.",
        "Memory access uses CPUSCR VLE instruction injection and scratch SRAM.",
    ),
)

SPC5748G = Chip(
    key="spc5748g",
    part="SPC5748GSMMJ6",
    nxp_twin="MPC5748G",
    family="Chorus (MPC574xG)",
    core="2x e200z4 + e200z2 + HSM (VLE, MMU)",
    jtagc_irlen=6,
    once_irlen=10,
    has_jcomp=True,
    has_mmu=True,
    read_presets=(
        {"key": "config", "name": "Config only · 32 KiB", "address": 0x00FA8000, "size": 0x8000, "filename": "MPC5748G_Config"},
        {"key": "full", "name": "Full flash · 5.94 MiB", "address": 0x00F90000, "size": 0x005F0000, "filename": "MPC5748G_Full"},
    ),
    sram_base=0x40000000,
    flash_ctrl={"FMC": 0xFFFE0000, "SWT0": 0xFC050000, "SWT_UTEST": 0xFC058000,
                "PASS": 0xFFFF4000},
    regions=(
        FlashRegion("CFLASH", 0x00F90000, 0x005F0000,
                    "small 0x00F90000-0x00FFFFFF (8x16k,6x32k,2x64k) + 22x256k @0x01000000+"),
        FlashRegion("UTEST", 0x00400000, 0x00004000,
                    "OTP: JTAG pw 0x400120, group pw 0x400140/160/180/1A0, DCF @0x400300"),
        FlashRegion("BAF", 0x00404000, 0x00004000, "Boot Assist Flash"),
        FlashRegion("HSMData", 0x00F80000, 0x00008000, ""),
        FlashRegion("HSMCode", 0x00610000, 0x00024000, "+ small block 0x00F8C000"),
    ),
    unlock={
        # C55 FMC register offsets from FMC base (struct C55FMC_tag, MPC5748G.h)
        "FMC_MCR": 0x00, "FMC_MCRE": 0x08,
        "FMC_LOCK0": 0x10, "FMC_LOCK1": 0x14, "FMC_LOCK2": 0x18, "FMC_LOCK3": 0x1C,
        "FMC_SEL0": 0x38, "FMC_SEL1": 0x3C, "FMC_SEL2": 0x40, "FMC_SEL3": 0x44,
        "FMC_ADR": 0x50,
        # MCR bit masks (verified from header bitfield)
        "MCR_EER": 0x00008000, "MCR_RWE": 0x00004000, "MCR_DONE": 0x00000400,
        "MCR_PEG": 0x00000200, "MCR_PGM": 0x00000010, "MCR_ERS": 0x00000004,
        "MCR_EHV": 0x00000001,
        # PASS module offsets from PASS base (struct PASS_tag)
        "PASS_LCSTAT": 0x00, "PASS_CHSEL": 0x08, "PASS_CSTAT": 0x10,
        "PASS_CIN0": 0x20, "PASS_LOCK_PG0": 0x100,
    },
    notes=(
        "JCOMP must be driven HIGH to enable the JTAGC.",
        "Censored or PASS-protected devices require authentication not implemented here.",
        "ERASE must write SEL0-3 (+0x38..) not just LOCK0-3 (+0x10..) or nothing erases.",
        "UTEST, BAF, HSM, DCF and lifecycle modifications are excluded.",
    ),
)

# ST's automotive OpenOCD target uses the boot core (CORE_2 / 0x2A),
# Nexus access attributes 3<<22, and SWT at F4058000. Do not apply the
# MPC5748G peripheral setup to this device. DFLASH range also matches the
# user's ST_SPC58NH92_1x32x64k_DFlash.pcp. No proprietary loader is executed.
SPC58NH92 = Chip(
    key="spc58nh92", part="SPC58NH92", nxp_twin="ST SPC58NH · DFLASH read only",
    family="Chorus H-line", core="3x e200z4 · boot core 2",
    jtagc_irlen=6, once_irlen=10, has_jcomp=True, has_mmu=True,
    sram_base=0x40028000, flash_ctrl={"FMC": 0xF7FE0000, "SWT": 0xF4058000},
    regions=(FlashRegion("DFLASH", 0x00800000, 0x40000, "256 KiB · read only"),),
    read_only=True, core_selectors=(0x2A,), nexus_access=3 << 22,
    idcodes=(0x01146041,),
    read_presets=({"key": "dflash", "name": "DFLASH only · 256 KiB", "address": 0x00800000,
                   "size": 0x40000, "filename": "SPC58NH92_DFLASH"},),
    notes=("Hardware read support has not yet been tested on an SPC58NH92.",
           "Only DFLASH is exposed; erase, write and program are disabled.",
           "14-pin connector follows the user's MPC5748G wiring reference."),
)

CHIPS = {c.key: c for c in (SPC560B64, SPC5748G, SPC58NH92)}


def get(key: str) -> Chip:
    try:
        return CHIPS[key]
    except KeyError:
        raise SystemExit(f"unknown chip '{key}'. known: {', '.join(CHIPS)}")
