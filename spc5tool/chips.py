"""Device descriptors. Executable erase geometry lives in geometry.py.

Addresses and capabilities are explicit per family. See docs/VALIDATION.md
for sources and the limited MPC5748G bench record. New OTP, shadow, password,
and SPC58 programming paths have offline verification only.
"""

from __future__ import annotations
from dataclasses import dataclass, field, replace

# JEDEC bank-0 manufacturer id for NXP/Freescale is 0x00E, so the low 12 bits of a
# valid 1149.1 IDCODE (manufacturer<<1 | 1) are 0x01D.
NXP_IDCODE_LOW12 = 0x01D


@dataclass(frozen=True)
class FlashRegion:
    name: str
    base: int
    size: int
    note: str = ""
    kind: str = "flash"
    writable: bool = True
    # None: ordinary programming covers the mapped region. Otherwise only the
    # listed (address, size) customer windows are accepted by generic Program.
    # Security provisioning has its own exact-slot validation.
    program_ranges: tuple | None = None


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
    algorithm: str = "c90-b"
    program_unit: int = 8
    password_bits: int = 64
    password_protocol: str = "censor64"
    watchdogs: tuple = (0xFFF38000,)
    buffering: tuple = ((0xC3F8801C, 0x19), (0xC3F88020, 1))
    debug_instruction_base: int | None = None


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
    # MPC5607B RM Table 3-1 / section 5.2: 16 KiB VLE Boot Assist ROM.
    # Both the injected PC and its following fetch stay inside this ROM.
    debug_instruction_base=0xFFFFC000,
    flash_ctrl={"CFLASH": 0xC3F88000, "DFLASH": 0xC3F8C000, "SWT": 0xFFF38000},
    regions=(
        FlashRegion("CFLASH", 0x00000000, 0x00180000,
                    "Low0-5:1x32k,2x16k,2x32k,1x128k; Mid0-1:2x128k; High0-7:8x128k"),
        FlashRegion("DFLASH", 0x00800000, 0x00010000, "Low0-3: 4x16k"),
        FlashRegion("SHADOW", 0x00200000, 0x00004000,
                    "Contains password and boot configuration; preserve valid censorship words.", "shadow"),
        FlashRegion("CFLASH_OTP", 0x00400000, 0x2000, "8 KiB user OTP; no erase", "otp"),
        FlashRegion("DFLASH_OTP", 0x00C00000, 0x2000, "8 KiB user OTP; no erase", "otp"),
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
        "Memory access uses CPUSCR VLE injection with a Boot Assist ROM fetch; no scratch SRAM.",
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
    algorithm="c55-mpc5748", password_bits=256, password_protocol="jtag256",
    watchdogs=(0xFC050000, 0xFC054000, 0xFC058000),
    buffering=((0xFC030000, 0x51), (0xFC030004, 0x51), (0xFC030018, 0x51)),
    read_presets=(
        {"key": "config", "name": "Config only · 32 KiB", "address": 0x00FA8000, "size": 0x8000, "filename": "MPC5748G_Config"},
        {"key": "full", "name": "Full flash · 5.94 MiB", "address": 0x00F90000, "size": 0x005F0000, "filename": "MPC5748G_Full"},
        {"key": "utest", "name": "UTEST config + user · excludes ECC tests", "address": 0x004000A0,
         "size": 0x3F60, "filename": "MPC5748G_UTEST_0x004000A0", "region_default": "UTEST"},
        {"key": "utest_user", "name": "UTEST user OTP · 12 KiB", "address": 0x00401000,
         "size": 0x3000, "filename": "MPC5748G_UTEST_User_0x00401000"},
    ),
    sram_base=0x40000000,
    flash_ctrl={"FMC": 0xFFFE0000, "SWT0": 0xFC050000, "SWT_UTEST": 0xFC058000,
                "PASS": 0xFFFF4000},
    regions=(
        FlashRegion("CFLASH", 0x00F90000, 0x005F0000,
                    "small 0x00F90000-0x00FFFFFF (8x16k,6x32k,2x64k) + 22x256k @0x01000000+"),
        FlashRegion("UTEST", 0x00400000, 0x00004000,
                    "Default read starts at 0x004000A0, excluding factory ECC tests. "
                    "Program once: user OTP 0x00401000–0x00403FFF only. "
                    "Use Install password for password slots. No erase.", "otp",
                    program_ranges=((0x401000, 0x3000),)),
        FlashRegion("BAF", 0x00404000, 0x00004000, "Factory Boot Assist Flash", "rom", False),
        FlashRegion("HSMData", 0x00F80000, 0x00008000, "Security-dependent access; write backend unavailable", "security", False),
        FlashRegion("HSMCode", 0x00610000, 0x00020000, "Two 64 KiB blocks; write backend unavailable", "security", False),
        FlashRegion("HSMCodeSmall", 0x00F8C000, 0x00004000, "Separate HSM code block; write backend unavailable", "security", False),
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
        "256-bit JTAG password entry supported; unlocking a protected target awaits bench validation.",
        "PASS group authentication is separate from JTAG password entry and is not implemented.",
        "ERASE must write SEL0-3 (+0x38..) not just LOCK0-3 (+0x10..) or nothing erases.",
        "UTEST supports program-once; BAF and HSM are read-only in this driver.",
    ),
)

# ST's automotive OpenOCD target uses the boot core (CORE_2 / 0x2A),
# Nexus access attributes 3<<22, and SWT at F4058000. Do not apply the
# MPC5748G peripheral setup to this device. DFLASH range also matches the
# user's ST_SPC58NH92_1x32x64k_DFlash.pcp. No proprietary loader is executed.
SPC58NH92 = Chip(
    key="spc58nh92", part="SPC58NH92", nxp_twin="ST SPC58NH · 10 MiB code + 256 KiB data",
    family="Chorus H-line · cut 2.0", core="3x e200z4 · boot core 2",
    jtagc_irlen=6, once_irlen=10, has_jcomp=True, has_mmu=True,
    algorithm="c55-spc58h", program_unit=16, password_bits=256, password_protocol="",
    watchdogs=(0xF4058000,), buffering=(),
    sram_base=0x40028000, flash_ctrl={"FMC": 0xF7FE0000, "SWT": 0xF4058000},
    regions=(FlashRegion("DFLASH", 0x00800000, 0x40000, "4 × 64 KiB"),
             FlashRegion("CFLASH", 0x00FC0000, 0xA00000, "10 MiB; 128-bit ECC programming"),
             FlashRegion("UTEST", 0x00400000, 0x4000,
                         "Program once: customer OTP 0x00401000–0x00401FFF only. "
                         "Use Install password for password slots. Factory ECC tests at "
                         "0x00400280–0x004002FF prevent a reliable full raw read. No erase.",
                         "otp", program_ranges=((0x401000, 0x1000),)),
             FlashRegion("BAF", 0x00404000, 0x4000, "Factory boot firmware", "rom", False),
             FlashRegion("HSMCode", 0x00608000, 0x30000, "HSM access depends on security configuration; write backend unavailable", "security", False),
             FlashRegion("HSMData", 0x00680000, 0x8000, "HSM access depends on security configuration; write backend unavailable", "security", False)),
    core_selectors=(0x2A, 0x28, 0x29), nexus_access=3 << 22,
    idcodes=(0x01146041,),
    read_presets=({"key": "dflash", "name": "DFLASH only · 256 KiB", "address": 0x00800000,
                   "size": 0x40000, "filename": "SPC58NH92_DFLASH"},
                  {"key": "cflash", "name": "CFLASH · 10 MiB", "address": 0xFC0000,
                   "size": 0xA00000, "filename": "SPC58NH92_CFLASH"},
                  {"key": "utest_user", "name": "UTEST customer OTP · 4 KiB", "address": 0x401000,
                   "size": 0x1000, "filename": "SPC58NH92_UTEST_User_0x00401000", "region_default": "UTEST"}),
    notes=("SPC58NH hardware validation pending; register sequences tested offline.",
           "Cut 2.0 only: IDCODE 0x01146041; other revisions require a qualified profile.",
           "UTEST cannot be erased; each ECC unit can be programmed only once.",
           "14-pin connector follows the user's MPC5748G wiring reference."),
)

def _bolero_variant(key, part, twin, size):
    return replace(SPC560B64, key=key, part=part, nxp_twin=twin,
                   core_selectors=(0x11,), regions=(
                       replace(SPC560B64.regions[0], size=size, note="RM0037 device-specific code flash capacity"),
                       *SPC560B64.regions[1:]))


SPC560B54 = _bolero_variant("spc560b54", "SPC560B54", "MPC5605B", 0xC0000)
SPC560B60 = _bolero_variant("spc560b60", "SPC560B60", "MPC5606B", 0x100000)
SPC58EH92 = replace(SPC58NH92, key="spc58eh92", part="SPC58EH92",
                    nxp_twin="ST SPC58EH · 10 MiB code + 256 KiB data",
                    core="2x e200z4 · boot core 2", core_selectors=(0x2A, 0x28),
                    read_presets=tuple({**p, "filename": p["filename"].replace("NH", "EH")}
                                       for p in SPC58NH92.read_presets))

CHIPS = {c.key: c for c in (SPC560B64, SPC560B54, SPC560B60, SPC5748G, SPC58NH92, SPC58EH92)}


def get(key: str) -> Chip:
    try:
        return CHIPS[key]
    except KeyError:
        raise SystemExit(f"unknown chip '{key}'. known: {', '.join(CHIPS)}")
