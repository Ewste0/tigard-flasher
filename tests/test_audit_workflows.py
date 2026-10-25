"""Failure-path audit regressions. No adapter or target is opened."""
import unittest
import tempfile
from unittest.mock import patch

from spc5tool.chips import SPC560B64, SPC5748G, SPC58NH92
from spc5tool.errors import DriverError
from spc5tool.flash import Flash
from spc5tool.geometry import blocks_for
from spc5tool.mpsse import MpsseError
from tests.simulator import SimMemory
from tests.backends import tigard_descriptor
from tests.test_images import record
from spc5tool.web import Workspace


class LockRestoreTests(unittest.TestCase):
    def test_lost_usb_during_restore_stops_all_remaining_io(self):
        for chip in (SPC560B64, SPC5748G, SPC58NH92):
            for fail_at in (1, 2) if chip == SPC560B64 else (1,):
                with self.subTest(chip=chip.key, restore_transfer=fail_at):
                    memory = SimMemory(chip)
                    flash = Flash(memory, chip)
                    blocks = [b for b in blocks_for(chip) if b.kind == "flash"]
                    # Multiple lock banks ensure there is cleanup left to skip.
                    selected = [blocks[0], blocks[-1]]
                    restoring = False
                    accesses = []
                    read, write = memory.read32, memory.write32

                    def read32(address):
                        if restoring:
                            accesses.append(("read", address))
                        return read(address)

                    def write32(address, value):
                        if restoring:
                            accesses.append(("write", address, value))
                            if len(accesses) >= fail_at:
                                raise MpsseError("USB lost during lock restore")
                        return write(address, value)

                    memory.read32, memory.write32 = read32, write32
                    with self.assertRaisesRegex(DriverError, "could not restore flash locks") as error:
                        with flash._unlocked(selected):
                            restoring = True
                    self.assertIsInstance(error.exception.__cause__, MpsseError)
                    self.assertEqual(len(accesses), fail_at)
                    self.assertTrue(flash.poisoned)
                    with self.assertRaisesRegex(DriverError, "reconnect/reset"):
                        flash.read(selected[0].address, chip.program_unit)
                    self.assertEqual(len(accesses), fail_at)


class ReviewedImageTests(unittest.TestCase):
    def test_altered_images_are_rejected_before_opening_hardware(self):
        binary = b"abcdefghABCDEFGH"
        srec = record('3', 0x800000, binary[:8]) + record('3', 0x800010, binary[8:])
        for action in ('write', 'program', 'verify'):
            for name, uploaded in (('image.bin', binary), ('image.s19', srec)):
                for edited in (b'Z' + binary[1:], binary[:-1], binary + b'Z'):
                    with self.subTest(action=action, name=name, length=len(edited)), \
                         tempfile.TemporaryDirectory() as directory, \
                         patch('spc5tool.web.discover_tigards', return_value=[tigard_descriptor()]):
                        workspace = Workspace(directory)
                        info = workspace.add_file(name, uploaded)
                        plan = workspace.plan({'target': 'spc560b64', 'action': action,
                            'mode': 'hardware', 'address': 0x800000, 'length': 16,
                            'file_id': info['id'], 'segment': 0, 'expected_id': 0x0AE4301D})
                        workspace.file(info['id'])[1].write_bytes(edited)
                        job = {'id': 'audit', 'plan': plan, 'logs': [], 'result': {}, 'progress': 0}
                        with patch.object(workspace, '_backend') as backend:
                            workspace._run(job)
                        backend.assert_not_called()
                        self.assertEqual(job['status'], 'failed')
                        self.assertIn('image changed', job['error'])
                        self.assertNotIn('backup', job['result'])


if __name__ == "__main__":
    unittest.main()
