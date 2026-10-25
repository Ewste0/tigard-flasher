"""Grouped main-array Write integrity and read-traffic regressions, no USB."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from spc5tool.chips import CHIPS
from spc5tool.cli import main
from spc5tool.errors import VerificationError
from spc5tool.flash import Flash
from spc5tool.geometry import blocks_for
from tests.simulator import SimMemory


class GroupedWriteTests(unittest.TestCase):
    def test_cli_grouped_erase_plan_and_verified_timing_result(self):
        for chip in CHIPS.values():
            with self.subTest(chip=chip.key), tempfile.TemporaryDirectory() as directory:
                blocks = [b for b in blocks_for(chip) if b.region == "CFLASH"][:2]
                request = ["erase", chip.key, "--address", hex(blocks[0].address),
                           "--length", hex(sum(b.size for b in blocks))]
                with patch("spc5tool.cli.backend") as backend, contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(main(request), 0)
                backend.assert_not_called()
                self.assertEqual(json.loads(out.getvalue())["erase_batches"], 1)
                memory = SimMemory(chip)
                flash = Flash(memory, chip)
                with patch("spc5tool.cli.backend", return_value=contextlib.nullcontext(flash)), \
                     contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(main(request + ["--execute", "--expect-idcode", "0x0988101D",
                        "--backup", str(Path(directory) / "backup")]), 0)
                result = json.loads(out.getvalue())
                self.assertTrue(result["verified"])
                self.assertEqual(result["erase_stats"]["batches"], 1)
                self.assertEqual(result["erase_stats"]["blocks"], 2)
                self.assertGreaterEqual(result["erase_stats"]["backup_seconds"], 0)
                self.assertTrue((Path(directory) / "backup" / "manifest.json").is_file())

    def test_sparse_changed_blocks_share_erase_and_preserve_complete_arrays(self):
        for chip in CHIPS.values():
            for region in (r for r in chip.regions if r.kind == "flash"):
                with self.subTest(chip=chip.key, region=region.name):
                    memory = SimMemory(chip)
                    flash = Flash(memory, chip)
                    first, middle, last = [b for b in blocks_for(chip) if b.region == region.name][:3]
                    storage = memory.regions[region.base]
                    storage[first.address - region.base] = 0x35
                    storage[middle.address - region.base:middle.address - region.base + 8] = b"preserve"
                    storage[last.end - region.base - 1] = 0xA6
                    before = bytes(storage)
                    address, end = first.end - 8, last.address + 8
                    payload = bytearray(before[address - region.base:end - region.base])
                    payload[:8], payload[-8:] = b"changed1", b"changed2"
                    expected = bytearray(before)
                    expected[address - region.base:end - region.base] = payload
                    backup, progress = [], []
                    flash.progress = lambda _, done, total: progress.append(done / total)
                    def save(items):
                        self.assertFalse(any(a == first.controller and v & 1 for a, v in memory.log))
                        backup.extend(items)
                    with patch.object(memory, "read", wraps=memory.read) as reads:
                        flash.patch(address, bytes(payload), save)
                    self.assertEqual(storage, expected)
                    self.assertEqual(b"".join(data for _, data in backup),
                                     before[first.address - region.base:last.end - region.base])
                    self.assertEqual(sum(c.args[1] for c in reads.call_args_list),
                                     3 * (first.size + last.size) + 2 * middle.size)
                    self.assertEqual(sum(a == first.controller and v == 5 for a, v in memory.log), 1)
                    selector = first.controller + (0x38 + 4 * first.bank
                                                   if chip.algorithm.startswith("c55") else 0x10)
                    self.assertIn((selector, first.mask | last.mask), memory.log)
                    self.assertFalse(any(middle.address <= a < middle.end for a, _ in memory.log))
                    self.assertEqual(flash.write_stats["changed_blocks"], 2)
                    self.assertEqual(flash.write_stats["unchanged_blocks"], 1)
                    self.assertEqual(flash.write_stats["erase_batches"], 1)
                    self.assertEqual(progress, sorted(progress))
                    self.assertEqual(progress[-1], 1)

    def test_failed_backup_prevents_all_controller_mutation(self):
        for chip in CHIPS.values():
            memory = SimMemory(chip)
            flash = Flash(memory, chip)
            block = next(b for b in blocks_for(chip) if b.region == "CFLASH")
            with self.assertRaisesRegex(OSError, "disk full"):
                flash.patch(block.address, b"changed!", Mock(side_effect=OSError("disk full")))
            self.assertTrue(all(a == block.controller and v == 0xC000 for a, v in memory.log))

    def test_one_failed_blank_block_prevents_programming_entire_batch(self):
        for chip in CHIPS.values():
            with self.subTest(chip=chip.key):
                memory = SimMemory(chip)
                flash = Flash(memory, chip)
                blocks = [b for b in blocks_for(chip) if b.region == "CFLASH"][:2]
                region = next(r for r in chip.regions if r.name == "CFLASH")
                memory.regions[region.base][blocks[1].address - region.base] = 0
                unlocked = memory._unlocked
                memory._unlocked = lambda b: b[0] != blocks[1].address and unlocked(b)
                with self.assertRaises(VerificationError):
                    flash.patch(blocks[0].end - 8, b"new data" * 2, lambda _: None)
                self.assertFalse(any(a == blocks[0].controller and v == 0x11 for a, v in memory.log))

    def test_final_verification_covers_preserved_bytes_on_every_family(self):
        for chip in CHIPS.values():
            with self.subTest(chip=chip.key):
                memory = SimMemory(chip)
                flash = Flash(memory, chip)
                region = next(r for r in chip.regions if r.name == "CFLASH")
                program = flash._program_erased
                def corrupt(*args):
                    program(*args)
                    memory.regions[region.base][0x1000] = 0
                flash._program_erased = corrupt
                with self.assertRaises(VerificationError):
                    flash.patch(region.base, b"new data", lambda _: None)


if __name__ == "__main__":
    unittest.main()
