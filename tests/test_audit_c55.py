"""Offline C55 audit vectors; these do not certify physical OTP programming.

Maps: NXP AN12092 fig. 3 and ST RM0452 tables 6/14, cross-checked against
ST's automotive OpenOCD spc58xh.c low/large block selectors. Public register
contracts only: no proprietary programming loader or MCU contents are used.
"""
import unittest

from spc5tool.chips import SPC5748G, SPC58NH92, SPC58EH92
from spc5tool.errors import DriverError
from spc5tool.flash import Flash
from spc5tool.geometry import blocks_for, select_blocks
from tests.simulator import SimMemory


C55_CHIPS = (SPC5748G, SPC58NH92, SPC58EH92)

# Address, length, register bank, LSB-zero selector position. Keep the unusual
# H-line low-block order explicit so sequential-mask regressions are visible.
MPC_SMALL = (
    (0xF90000, 0x4000, 0, 0), (0xF94000, 0x4000, 0, 1),
    (0xF98000, 0x4000, 0, 2), (0xF9C000, 0x4000, 0, 3),
    (0xFA0000, 0x4000, 0, 4), (0xFA4000, 0x4000, 0, 5),
    (0xFA8000, 0x4000, 0, 6), (0xFAC000, 0x4000, 0, 7),
    (0xFB0000, 0x8000, 0, 8), (0xFB8000, 0x8000, 0, 9),
    (0xFC0000, 0x8000, 0, 18), (0xFC8000, 0x8000, 0, 19),
    (0xFD0000, 0x8000, 0, 20), (0xFD8000, 0x8000, 0, 21),
    (0xFE0000, 0x10000, 0, 22), (0xFF0000, 0x10000, 0, 24),
)
H_SMALL = (
    (0xFC0000, 0x4000, 0, 19), (0xFC4000, 0x4000, 0, 17),
    (0xFC8000, 0x4000, 0, 20), (0xFCC000, 0x4000, 0, 18),
    (0xFD0000, 0x8000, 0, 22), (0xFD8000, 0x8000, 0, 23),
    (0xFE0000, 0x10000, 0, 24), (0xFF0000, 0x10000, 0, 25),
    (0x1000000, 0x20000, 2, 0), (0x1020000, 0x20000, 2, 1),
)


class C55AuditTests(unittest.TestCase):
    def test_all_code_and_data_blocks_match_reference_vectors(self):
        expected_mpc = list(MPC_SMALL) + [
            (0x1000000 + n * 0x40000, 0x40000, 2, n) for n in range(22)]
        expected_h = list(H_SMALL) + [
            (0x1040000 + n * 0x40000, 0x40000,
             2 if n < 30 else 3, (n + 2) % 32) for n in range(38)]
        for chip in C55_CHIPS:
            with self.subTest(chip=chip.key):
                blocks = blocks_for(chip)
                code = [b for b in blocks if b.region == "CFLASH"]
                expected = expected_mpc if chip is SPC5748G else expected_h
                actual = [(b.address, b.size, b.bank, b.mask) for b in code]
                self.assertEqual(actual, [(a, s, bank, 1 << bit)
                                          for a, s, bank, bit in expected])
                self.assertEqual(len(code), 38 if chip is SPC5748G else 48)
                for left, right in zip(code, code[1:]):
                    self.assertEqual(left.end, right.address)
                data = [b for b in blocks if b.region == "DFLASH"]
                self.assertEqual([(b.address, b.size, b.bank, b.mask) for b in data],
                                 [] if chip is SPC5748G else
                                 [(0x800000 + n * 0x10000, 0x10000, 1, 1 << n)
                                  for n in range(4)])
                code_region = next(r for r in chip.regions if r.name == "CFLASH")
                self.assertEqual(sum(b.size for b in code), code_region.size)
                self.assertEqual(code[-1].end, code_region.base + code_region.size)
                for block in blocks:
                    self.assertEqual(block.controller, chip.flash_ctrl["FMC"])
                    self.assertEqual(select_blocks(chip, block.address, block.size), (block,))

    def test_every_erase_block_selects_only_itself_and_restores_locks(self):
        for chip in C55_CHIPS:
            memory = SimMemory(chip)
            flash = Flash(memory, chip)
            base = chip.flash_ctrl["FMC"]
            regs = memory.controllers[base]["regs"]
            originals = {0x10: 0xE5A5A5A5, 0x14: 0xC3C3C3C3,
                         0x18: 0xF0F0F0F0, 0x1C: 0x96969696}
            regs.update(originals)
            for block in blocks_for(chip):
                if block.kind != "flash":
                    continue
                with self.subTest(chip=chip.key, block=block.name):
                    region = next(r for r in chip.regions if r.name == block.region)
                    storage = memory.regions[region.base]
                    offset = block.address - region.base
                    storage[offset:offset + block.size] = b"\x00" * block.size
                    # Canaries in adjacent sectors must survive this erase.
                    if offset:
                        storage[offset - 1] = 0x35
                    if offset + block.size < len(storage):
                        storage[offset + block.size] = 0xA6
                    memory.log.clear()
                    flash.erase(block.address, block.size)
                    self.assertEqual(storage[offset:offset + block.size], b"\xff" * block.size)
                    if offset:
                        self.assertEqual(storage[offset - 1], 0x35)
                    if offset + block.size < len(storage):
                        self.assertEqual(storage[offset + block.size], 0xA6)
                    selectors = [(a - base, v) for a, v in memory.log
                                 if base + 0x38 <= a <= base + 0x44]
                    self.assertEqual(selectors, [(0x38 + 4 * bank,
                                                  block.mask if bank == block.bank else 0)
                                                 for bank in range(4)])
                    self.assertEqual({o: regs[o] for o in originals}, originals)
                    self.assertEqual(sum(a == base and v == 5 for a, v in memory.log), 1)

    def test_customer_otp_uses_complete_units_and_never_quad_page_mode(self):
        for chip in C55_CHIPS:
            with self.subTest(chip=chip.key):
                memory = SimMemory(chip)
                flash = Flash(memory, chip)
                flash.bulk_program = True
                unit = 8 if chip is SPC5748G else 16
                self.assertEqual(chip.program_unit, unit)
                # Include one deliberately blank granule. No pulse must consume it.
                payload = bytes(range(unit)) + b"\xff" * unit + bytes(range(unit, 2 * unit))
                flash.program(0x401000, payload)
                self.assertEqual(memory.read(0x401000, len(payload)), payload)
                self.assertEqual(memory.read(0x401000 - unit, unit), b"\xff" * unit)
                self.assertEqual(memory.read(0x401000 + len(payload), unit), b"\xff" * unit)
                base = chip.flash_ctrl["FMC"]
                self.assertEqual(sum(a == base and v == 0x11 for a, v in memory.log), 2)
                self.assertFalse(any(a == base and v & 4 for a, v in memory.log))
                locks = [(a, v) for a, v in memory.log if base + 0x10 <= a <= base + 0x1C]
                self.assertEqual(locks, [(base + 0x10, 0x7FFFFFFF),
                                        (base + 0x10, 0xFFFFFFFF)])
                # A later request touching a used granule must fail before any
                # unlock/EHV, even if another requested granule is still blank.
                memory.log.clear()
                with self.assertRaises(DriverError):
                    flash.program(0x401000, b"\x00" * (4 * unit))
                self.assertTrue(all(a == base and v == 0xC000 for a, v in memory.log))

    def test_otp_erase_and_factory_writes_are_rejected_without_io(self):
        for chip in C55_CHIPS:
            memory = SimMemory(chip)
            flash = Flash(memory, chip)
            for region in chip.regions:
                with self.subTest(chip=chip.key, region=region.name):
                    memory.log.clear()
                    if region.kind == "otp":
                        with self.assertRaises(ValueError):
                            flash.erase(region.base, region.size)
                    elif not region.writable:
                        with self.assertRaises(ValueError):
                            flash.program(region.base, bytes(chip.program_unit))
                        with self.assertRaises(ValueError):
                            flash.erase(region.base, region.size)
                    self.assertEqual(memory.log, [])

    def test_read_bounds_do_not_bridge_unmapped_gaps(self):
        for chip in C55_CHIPS:
            regions = sorted(chip.regions, key=lambda r: r.base)
            for left, right in zip(regions, regions[1:]):
                if left.base + left.size == right.base:
                    continue
                with self.subTest(chip=chip.key, left=left.name, right=right.name):
                    with self.assertRaises(ValueError):
                        select_blocks(chip, left.base + left.size - 1,
                                      right.base - left.base - left.size + 2)


if __name__ == "__main__":
    unittest.main()
