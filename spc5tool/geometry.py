"""Explicit erase geometry; masks use ordinary LSB-zero integer numbering.

Sources: MPC5607BRM rev 7.1 tables 30-2/3, figures 30-4/10;
AN12092 figure 3 (MPC5748G), MPC5748G.h C55FMC register layout.
Security, test, HSM, BAF and shadow blocks are intentionally absent.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Block:
    name: str
    region: str
    address: int
    size: int
    controller: int
    bank: int
    mask: int

    @property
    def end(self):
        return self.address + self.size


def blocks_for(chip):
    blocks = []
    if chip.key == "spc560b64":
        address = 0
        for i, size in enumerate((0x8000, 0x4000, 0x4000, 0x8000, 0x8000, 0x20000)):
            blocks.append(Block(f"CFLASH.L{i}", "CFLASH", address, size, 0xC3F88000, 0, 1 << i))
            address += size
        for i in range(2):
            # MSL/MLK are bits 17:16, NOT bits 7:6.
            blocks.append(Block(f"CFLASH.M{i}", "CFLASH", 0x40000 + i * 0x20000,
                                0x20000, 0xC3F88000, 0, 1 << (16 + i)))
        for i in range(8):
            blocks.append(Block(f"CFLASH.H{i}", "CFLASH", 0x80000 + i * 0x20000,
                                0x20000, 0xC3F88000, 1, 1 << i))
        for i in range(4):
            blocks.append(Block(f"DFLASH.L{i}", "DFLASH", 0x800000 + i * 0x4000,
                                0x4000, 0xC3F8C000, 0, 1 << i))
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
    elif chip.key == "spc58nh92":
        # One readable window, NOT an erase-sector description. There are no
        # unlock/erase masks; all modifying entry points reject this profile.
        blocks.append(Block("DFLASH (read only)", "DFLASH", 0x00800000, 0x40000, 0xF7FE0000, 0, 0))
    else:
        raise ValueError(f"no flash algorithm for {chip.key}")
    return tuple(blocks)


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
            raise ValueError("range crosses unmapped or protected flash")
        cursor = block.end
    if not selected or cursor < address + size:
        raise ValueError("range is outside supported main/data flash; protected regions cannot be modified")
    if whole and (selected[0].address != address or selected[-1].end != address + size):
        raise ValueError("erase range must contain whole blocks (use 'plan' to see boundaries)")
    return selected
