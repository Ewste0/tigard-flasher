"""Explicit erase geometry; masks use ordinary LSB-zero integer numbering.

Sources: MPC5607BRM rev 7.1 tables 30-2/3, figures 30-4/10;
AN12092 figure 3 (MPC5748G), MPC5748G.h C55FMC register layout.
ST DS12304 and automotive OpenOCD supply SPC58H geometry. OTP and shadow
are separate capabilities; factory BAF/HSM descriptors are read-only.
"""
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Block:
    name: str
    region: str
    address: int
    size: int
    controller: int
    bank: int
    mask: int
    kind: str = "flash"
    writable: bool = True

    @property
    def end(self):
        return self.address + self.size


def backup_blocks(chip, address, size):
    """Back up erase blocks, or only touched ECC units for non-erasing OTP.

    Reading an entire UTEST block can hit intentional factory ECC errors or
    protected slots unrelated to the requested operation. OTP programming never
    erases a block, so its backup covers only the units the operation can change.
    These clipped descriptors are backup spans, never erase geometry.
    """
    blocks = select_blocks(chip, address, size)
    unit = chip.program_unit
    start = address // unit * unit
    end = (address + size + unit - 1) // unit * unit
    return tuple(replace(b, address=max(b.address, start),
                         size=min(b.end, end) - max(b.address, start))
                 if b.kind == "otp" else b for b in blocks)


def speed_blocks(chip, address, size):
    """Explicit no-backup/no-verify policy for complete SPC560B64 main blocks."""
    if chip.key != "spc560b64" or chip.algorithm != "c90-b":
        raise ValueError("Speed mode is available only for SPC560B64L3 CFLASH/DFLASH")
    blocks = select_blocks(chip, address, size)
    if blocks[0].address != address or blocks[-1].end != address + size:
        raise ValueError("Speed mode requires complete erase blocks. Select Standard for a partial image; no padding is added automatically.")
    if (any(b.kind != "flash" or not b.writable or b.region not in ("CFLASH", "DFLASH") for b in blocks)
            or len({b.controller for b in blocks}) != 1):
        raise ValueError("Speed mode requires one ordinary CFLASH/DFLASH region; SHADOW and OTP are excluded")
    return blocks


def blocks_for(chip):
    blocks = []
    if chip.algorithm == "c90-b":
        address = 0
        for i, size in enumerate((0x8000, 0x4000, 0x4000, 0x8000, 0x8000, 0x20000)):
            blocks.append(Block(f"CFLASH.L{i}", "CFLASH", address, size, 0xC3F88000, 0, 1 << i))
            address += size
        for i in range(2):
            # MSL/MLK are bits 17:16, NOT bits 7:6.
            blocks.append(Block(f"CFLASH.M{i}", "CFLASH", 0x40000 + i * 0x20000,
                                0x20000, 0xC3F88000, 0, 1 << (16 + i)))
        for i in range((chip.regions[0].size - 0x80000) // 0x20000):
            blocks.append(Block(f"CFLASH.H{i}", "CFLASH", 0x80000 + i * 0x20000,
                                0x20000, 0xC3F88000, 1, 1 << i))
        for i in range(4):
            blocks.append(Block(f"DFLASH.L{i}", "DFLASH", 0x800000 + i * 0x4000,
                                0x4000, 0xC3F8C000, 0, 1 << i))
        blocks.extend((
            Block("SHADOW", "SHADOW", 0x200000, 0x4000, 0xC3F88000, 0, 1 << 20, "shadow"),
            Block("CFLASH_OTP", "CFLASH_OTP", 0x400000, 0x2000, 0xC3F88000, 0, 1 << 20, "otp"),
            Block("DFLASH_OTP", "DFLASH_OTP", 0xC00000, 0x2000, 0xC3F8C000, 0, 1 << 20, "otp"),
        ))
    elif chip.key == "spc5748g":
        small = [(0xF90000 + i * 0x4000, 0x4000, i) for i in range(8)]
        small += [(0xFB0000, 0x8000, 8), (0xFB8000, 0x8000, 9)]
        small += [(0xFC0000 + i * 0x8000, 0x8000, 18 + i) for i in range(4)]
        small += [(0xFE0000, 0x10000, 22), (0xFF0000, 0x10000, 24)]
        for i, (address, size, bit) in enumerate(small):
            blocks.append(Block(f"CFLASH.S{i}", "CFLASH", address, size, 0xFFFE0000, 0, 1 << bit))
        for i in range(22):
            blocks.append(Block(f"CFLASH.B{i}", "CFLASH", 0x1000000 + i * 0x40000,
                                0x40000, 0xFFFE0000, 2, 1 << i))
    elif chip.algorithm == "c55-spc58h":
        base = chip.flash_ctrl["FMC"]
        for i in range(4):
            blocks.append(Block(f"DFLASH.H{i}", "DFLASH", 0x800000 + i * 0x10000,
                                0x10000, base, 1, 1 << i))
        address = 0xFC0000
        for i, (size, bit) in enumerate(zip((0x4000,) * 4 + (0x8000,) * 2 + (0x10000,) * 2,
                                           (19, 17, 20, 18, 22, 23, 24, 25))):
            blocks.append(Block(f"CFLASH.L{i}", "CFLASH", address, size, base, 0, 1 << bit))
            address += size
        end = next(r.base + r.size for r in chip.regions if r.name == "CFLASH")
        i = 0
        while address < end:
            size = 0x20000 if i < 2 else 0x40000
            blocks.append(Block(f"CFLASH.B{i}", "CFLASH", address, size, base,
                                2 + i // 32, 1 << (i % 32)))
            address += size
            i += 1
    else:
        raise ValueError(f"no flash algorithm for {chip.key}")
    if chip.algorithm.startswith("c55"):
        base = chip.flash_ctrl["FMC"]
        for region in chip.regions:
            if region.name in ("CFLASH", "DFLASH"):
                continue
            blocks.append(Block(region.name, region.name, region.base, region.size, base,
                                0, 1 << 31 if region.name == "UTEST" else 0,
                                region.kind, region.writable if region.name == "UTEST" else False))
    return tuple(sorted(blocks, key=lambda b: b.address))


def checked_range(address, size):
    if not isinstance(address, int) or not isinstance(size, int):
        raise ValueError("address and size must be integers")
    if address < 0 or size <= 0 or address + size > 0x100000000:
        raise ValueError("range must be nonempty and fit in the 32-bit address space")


def select_blocks(chip, address, size, *, whole=False):
    checked_range(address, size)
    selected = tuple(b for b in blocks_for(chip) if b.address < address + size and b.end > address)
    cursor = address
    for block in selected:
        if block.address > cursor:
            raise ValueError("range crosses unmapped flash")
        cursor = block.end
    if not selected or cursor < address + size:
        raise ValueError("range is outside the supported flash regions")
    if whole and (selected[0].address != address or selected[-1].end != address + size):
        raise ValueError("erase range must contain whole blocks (use 'plan' to see boundaries)")
    return selected


def validate_program_range(chip, address, size):
    """Validate ordinary Program before USB access, including customer OTP bounds.

    A blank UTEST cell is not permission to change a reserved, lifecycle or DCF
    field. Password installation uses an independently validated exact slot.
    This restriction does not reduce the physical map used for explicit reads.
    """
    blocks = select_blocks(chip, address, size)
    unit = chip.program_unit
    if address % unit or size % unit:
        raise ValueError(f"Program requires complete aligned {unit}-byte ECC units")
    for region in chip.regions:
        lo, hi = max(address, region.base), min(address + size, region.base + region.size)
        if lo >= hi or region.program_ranges is None:
            continue
        if not any(start <= lo and hi <= start + count for start, count in region.program_ranges):
            ranges = ", ".join(f"0x{start:08X}–0x{start + count - 1:08X}"
                               for start, count in region.program_ranges)
            raise ValueError(f"{region.name} Program is limited to customer OTP {ranges}. "
                             "Use Install password for password slots; raw factory, lifecycle "
                             "and DCF programming is not supported.")
    return blocks


def erase_groups(blocks):
    """Batch selected MAIN blocks within one controller/region, never OTP.

    C90LC UM1627 2.6.2 and C55 UM2636 2.7.2 permit combined main-array
    selectors. Separate C90 modules execute sequentially; SHADOW remains alone.
    The caller supplies reviewed whole-block descriptors from select_blocks().
    """
    groups = {}
    for block in blocks:
        if not block.writable or block.kind not in ("flash", "shadow"):
            raise ValueError(f"{block.region} cannot be erased")
        key = (block.controller, block.region,
               block.address if block.kind == "shadow" else None)
        groups.setdefault(key, []).append(block)
    if not groups:
        raise ValueError("erase needs at least one block")
    return tuple(tuple(group) for group in groups.values())
