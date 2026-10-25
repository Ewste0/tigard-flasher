"""Batched erase contracts, independently checking contents and bank masks.

Offline only. C55 main-interface multi-block selection: UM2636 2.7.2 and
MPC5748GRM 74.6.1.3. C90 LMS/HBS and shadow interlocks: MPC5607BRM 30.6.3.
The fixtures deliberately retain selected blocks despite PEG, reject selector
writes, and lose USB after EHV; none of those cases may report success.
"""
import unittest
from unittest.mock import patch

from spc5tool.chips import CHIPS, SPC5748G
from spc5tool.errors import DriverError, OperationTimeout, VerificationError
from spc5tool.flash import Flash
from spc5tool.geometry import blocks_for, erase_groups
from spc5tool.mpsse import MpsseError
from tests.simulator import SimMemory


class RecordingMemory(SimMemory):
    def __init__(self, chip):
        super().__init__(chip)
        self.pulses = []
        self.accesses = []
        self.ignored_block = None

    def read32(self, address):
        self.accesses.append(("read32", address))
        return super().read32(address)

    def read(self, address, size):
        self.accesses.append(("read", address, size))
        return super().read(address, size)

    def write32(self, address, value):
        self.accesses.append(("write32", address, value))
        return super().write32(address, value)

    def _unlocked(self, block):
        # Models a protection mechanism outside the readable volatile lock.
        return block[0] != self.ignored_block and super()._unlocked(block)

    def _execute(self, base, controller):
        self.pulses.append((base, controller["mcr"] & 0x14,
                            controller["interlock"], dict(controller["regs"])))
        super()._execute(base, controller)


def lock_values(memory):
    return {(base, offset): value
            for base, c in memory.controllers.items()
            for offset, value in c["regs"].items()
            if offset in ((0x10, 0x14, 0x18, 0x1C) if memory.c55 else (4, 8, 12))}


class BatchEraseTests(unittest.TestCase):
    def seed(self, chip):
        memory = RecordingMemory(chip)
        for region in memory.regions.values():
            region[:] = b"\xA5" * len(region)
        for base, offset in lock_values(memory):
            memory.controllers[base]["regs"][offset] = 0xA5A5A5A5
        return memory

    def assert_contents(self, memory, original, selected):
        for base, before in original.items():
            expected = bytearray(before)
            for block in selected:
                if base <= block.address < base + len(before):
                    offset = block.address - base
                    expected[offset:offset + block.size] = b"\xff" * block.size
            self.assertEqual(memory.regions[base], expected,
                             f"unexpected change in region 0x{base:08X}")

    def assert_selectors(self, chip, pulse, masks):
        base, mode, _, regs = pulse
        self.assertEqual(mode, 4)
        offsets = (0x38, 0x3C, 0x40, 0x44) if chip.algorithm.startswith("c55") else (
            (0x10,) if base == 0xC3F8C000 else (0x10, 0x14))
        self.assertEqual(tuple(regs[o] for o in offsets), masks)

    def test_full_regions_use_one_pulse_and_reference_masks(self):
        code_masks = {
            "spc560b54": (0x3003F, 0x03),
            "spc560b60": (0x3003F, 0x0F),
            "spc560b64": (0x3003F, 0xFF),
            "spc5748g": (0x017C03FF, 0, 0x003FFFFF, 0),
            "spc58nh92": (0x03DE0000, 0, 0xFFFFFFFF, 0xFF),
            "spc58eh92": (0x03DE0000, 0, 0xFFFFFFFF, 0xFF),
        }
        for chip in CHIPS.values():
            for region in chip.regions:
                if region.kind != "flash":
                    continue
                with self.subTest(chip=chip.key, region=region.name):
                    memory = self.seed(chip)
                    original = {a: bytes(d) for a, d in memory.regions.items()}
                    locks = lock_values(memory)
                    selected = [b for b in blocks_for(chip) if b.region == region.name]
                    events = []
                    flash = Flash(memory, chip, progress=lambda p, d, t: events.append((p, d / t)))
                    flash.erase(region.base, region.size)
                    self.assertEqual(len(memory.pulses), 1)
                    masks = code_masks[chip.key] if region.name == "CFLASH" else (
                        (0, 0xF, 0, 0) if memory.c55 else (0xF,))
                    self.assert_selectors(chip, memory.pulses[0], masks)
                    self.assertEqual(memory.pulses[0][2], region.base)
                    self.assert_contents(memory, original, selected)
                    self.assertEqual(lock_values(memory), locks)
                    fractions = [fraction for _, fraction in events]
                    self.assertTrue(fractions)
                    self.assertEqual(fractions[-1], 1)
                    self.assertEqual(fractions, sorted(fractions))
                    self.assertTrue(all(0 <= f <= 1 for f in fractions))
                    self.assertTrue(any("blank" in name for name, _ in events))

    def test_sparse_selection_preserves_every_gap_and_neighbor(self):
        for chip in CHIPS.values():
            if chip.algorithm == "c90-b":
                end = chip.regions[0].size
                starts = (0x8000, 0x40000, 0x80000, end - 0x20000)
                high = {"spc560b54": 0x3, "spc560b60": 0x9, "spc560b64": 0x81}[chip.key]
                masks = (0x10002, high)
            elif chip is SPC5748G:
                starts = (0xF94000, 0xFC8000, 0x1000000, 0x1540000)
                masks = (0x00080002, 0, 0x00200001, 0)
            else:
                starts = (0xFC4000, 0xFD8000, 0x1000000, 0x1780000, 0x17C0000, 0x1980000)
                masks = (0x00820000, 0, 0x80000001, 0x81)
            with self.subTest(chip=chip.key):
                selected = [b for b in blocks_for(chip) if b.address in starts]
                self.assertEqual(len(selected), len(starts))
                memory = self.seed(chip)
                original = {a: bytes(d) for a, d in memory.regions.items()}
                locks = lock_values(memory)
                Flash(memory, chip)._erase_blocks(selected)
                self.assertEqual(len(memory.pulses), 1)
                self.assert_selectors(chip, memory.pulses[0], masks)
                self.assertEqual(memory.pulses[0][2], starts[0])
                self.assert_contents(memory, original, selected)
                self.assertEqual(lock_values(memory), locks)

    def test_grouping_separates_regions_controllers_and_shadow(self):
        for chip in CHIPS.values():
            ordinary = [b for b in blocks_for(chip) if b.kind == "flash"]
            groups = erase_groups(ordinary)
            self.assertEqual(sum(len(group) for group in groups), len(ordinary))
            self.assertEqual(len(groups), 1 if chip is SPC5748G else 2)
            for group in groups:
                self.assertEqual(len({(b.controller, b.region) for b in group}), 1)
            if chip.algorithm == "c90-b":
                shadow = next(b for b in blocks_for(chip) if b.kind == "shadow")
                shadow_group, = [g for g in erase_groups([ordinary[0], shadow, ordinary[-1]])
                                if shadow in g]
                self.assertEqual(tuple(shadow_group), (shadow,))

    def test_successive_code_and_data_groups_clear_stale_selectors(self):
        for chip in CHIPS.values():
            if chip is SPC5748G:
                continue
            with self.subTest(chip=chip.key):
                selected = []
                for region in ("CFLASH", "DFLASH"):
                    blocks = [b for b in blocks_for(chip) if b.region == region]
                    selected.extend((blocks[0], blocks[-1]))
                memory = self.seed(chip)
                original = {a: bytes(d) for a, d in memory.regions.items()}
                locks = lock_values(memory)
                # An earlier operation may have selected any banks. The new
                # operation must replace those selectors, including zeroes.
                for base, controller in memory.controllers.items():
                    offsets = (0x38, 0x3C, 0x40, 0x44) if memory.c55 else (
                        (0x10,) if base == 0xC3F8C000 else (0x10, 0x14))
                    for offset in offsets:
                        controller["regs"][offset] = 0xFFFFFFFF
                events = []
                flash = Flash(memory, chip, progress=lambda p, d, t: events.append(d / t))
                flash._erase_blocks(selected)
                self.assertEqual(len(memory.pulses), 2)
                if memory.c55:
                    masks = ((0x00080000, 0, 0, 0x80), (0, 9, 0, 0))
                else:
                    last_high = {"spc560b54": 2, "spc560b60": 8, "spc560b64": 0x80}[chip.key]
                    masks = ((1, last_high), (9,))
                for pulse, expected in zip(memory.pulses, masks):
                    self.assert_selectors(chip, pulse, expected)
                self.assert_contents(memory, original, selected)
                self.assertEqual(lock_values(memory), locks)
                self.assertEqual(events, sorted(events))
                self.assertEqual(events[-1], 1)

    def test_forbidden_region_in_later_group_prevents_all_pulses(self):
        for chip in CHIPS.values():
            ordinary = next(b for b in blocks_for(chip) if b.kind == "flash")
            for blocked in blocks_for(chip):
                if blocked.kind != "otp" and blocked.writable:
                    continue
                with self.subTest(chip=chip.key, blocked=blocked.region):
                    with self.assertRaises(ValueError):
                        erase_groups([ordinary, blocked])
                    memory = self.seed(chip)
                    with self.assertRaises(ValueError):
                        Flash(memory, chip)._erase_blocks([ordinary, blocked])
                    self.assertEqual(memory.accesses, [])

    def test_peg_success_with_one_unerased_selected_block_fails_blank_check(self):
        for chip in CHIPS.values():
            with self.subTest(chip=chip.key):
                selected = [b for b in blocks_for(chip) if b.region == "CFLASH"][:2]
                memory = self.seed(chip)
                locks = lock_values(memory)
                memory.ignored_block = selected[-1].address
                with self.assertRaises(VerificationError):
                    Flash(memory, chip)._erase_blocks(selected)
                self.assertEqual(len(memory.pulses), 1)
                self.assertEqual(memory.read(selected[0].address, selected[0].size),
                                 b"\xff" * selected[0].size)
                self.assertEqual(memory.read(selected[-1].address, selected[-1].size),
                                 b"\xA5" * selected[-1].size)
                self.assertEqual(lock_values(memory), locks)

    def test_selector_that_does_not_latch_never_starts_high_voltage(self):
        for chip in CHIPS.values():
            with self.subTest(chip=chip.key):
                selected = [b for b in blocks_for(chip) if b.region == "CFLASH"][:2]
                memory = self.seed(chip)
                original = {a: bytes(d) for a, d in memory.regions.items()}
                locks = lock_values(memory)
                base = selected[0].controller
                selector = base + (0x38 if memory.c55 else 0x10)
                write = memory.write32
                def reject(address, value):
                    if address == selector:
                        memory.accesses.append(("rejected selector", address, value))
                        return
                    write(address, value)
                with patch.object(memory, "write32", side_effect=reject):
                    with self.assertRaisesRegex(DriverError, "selector"):
                        Flash(memory, chip)._erase_blocks(selected)
                self.assertEqual(memory.pulses, [])
                self.assert_contents(memory, original, [])
                self.assertEqual(lock_values(memory), locks)

    def test_usb_loss_after_ehv_stops_all_io_without_replay_or_cleanup(self):
        for chip in CHIPS.values():
            with self.subTest(chip=chip.key):
                selected = [b for b in blocks_for(chip) if b.region == "CFLASH"][:2]
                memory = self.seed(chip)
                flash = Flash(memory, chip)
                write = memory.write32
                marker = []
                def lose_reply(address, value):
                    write(address, value)
                    if address == selected[0].controller and value == 5:
                        marker.append(len(memory.accesses))
                        raise MpsseError("reply lost after EHV")
                with patch.object(memory, "write32", side_effect=lose_reply):
                    with self.assertRaises(MpsseError):
                        flash._erase_blocks(selected)
                self.assertEqual(marker, [len(memory.accesses)])
                self.assertEqual(len(memory.pulses), 1)
                self.assertTrue(flash.poisoned)

    def test_active_timeout_scales_but_cleanup_retains_single_operation_budget(self):
        class Clock:
            now = 0.0
            def monotonic(self):
                return self.now
            def sleep(self, _seconds):
                self.now += 1

        class SlowErase(RecordingMemory):
            def __init__(self, chip, clock, delay):
                super().__init__(chip)
                self.clock, self.delay, self.ready_at = clock, delay, None
            def _execute(self, base, controller):
                super()._execute(base, controller)
                self.ready_at = self.clock.now + self.delay
                controller["mcr"] &= ~0x400
            def read32(self, address):
                c = self.controllers.get(address)
                if c is not None and c["mcr"] & 1 and self.clock.now >= self.ready_at:
                    c["mcr"] |= 0x400
                return super().read32(address)

        selected = [b for b in blocks_for(SPC5748G) if b.region == "CFLASH"][:2]
        for delay, succeeds in ((12, True), (100, False)):
            with self.subTest(delay=delay):
                clock = Clock()
                memory = SlowErase(SPC5748G, clock, delay)
                flash = Flash(memory, SPC5748G, timeout=10)
                with patch("spc5tool.flash.time.monotonic", clock.monotonic), \
                        patch("spc5tool.flash.time.sleep", clock.sleep), \
                        patch.object(flash, "_wait_done", wraps=flash._wait_done) as waits:
                    if succeeds:
                        flash._erase_blocks(selected)
                    else:
                        with self.assertRaises(OperationTimeout):
                            flash._erase_blocks(selected)
                self.assertEqual(len(waits.call_args_list), 2)
                first, cleanup = waits.call_args_list
                active_timeout = first.kwargs.get("timeout", first.args[1] if len(first.args) > 1 else None)
                cleanup_timeout = cleanup.kwargs.get("timeout", cleanup.args[1] if len(cleanup.args) > 1 else None)
                self.assertEqual(active_timeout, 20)
                self.assertIn(cleanup_timeout, (None, 10))
                self.assertEqual(flash.timeout, 10)
                self.assertLessEqual(clock.now, 20)
                self.assertEqual(len(memory.pulses), 1)


if __name__ == "__main__":
    unittest.main()
