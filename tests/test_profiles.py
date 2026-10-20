"""Read scopes, region boundaries and ST-specific transport vectors."""
import contextlib
import io
import tempfile
import unittest
from unittest.mock import Mock, patch

from spc5tool.chips import SPC58NH92, SPC5748G
from spc5tool.cli import main
from spc5tool.errors import DriverError
from spc5tool.flash import Flash
from spc5tool.geometry import select_blocks
from spc5tool.memory import NexusMemory
from spc5tool.once import OnCE
from spc5tool.session import TargetSession
from tests.simulator import SimMemory
from spc5tool.web import Workspace, catalog


class ProfileTests(unittest.TestCase):
    def test_config_matches_pcp_and_full_stays_separate(self):
        config, full = SPC5748G.read_presets
        self.assertEqual((config['address'], config['size']), (0xFA8000, 0x8000))
        self.assertEqual([b.name for b in select_blocks(SPC5748G, config['address'], config['size'])], ['CFLASH.S6', 'CFLASH.S7'])
        self.assertEqual((full['address'], full['size']), (0xF90000, 0x5F0000))

    def test_spc58_dflash_boundaries_and_data_order(self):
        memory = SimMemory(SPC58NH92)
        flash = Flash(memory, SPC58NH92)
        memory.regions[0x800000][:] = bytes(range(256)) * 1024
        self.assertEqual(flash.read(0x800000, 0x40000), bytes(range(256)) * 1024)
        self.assertEqual(flash.read(0x83FFFD, 3), b'\xfd\xfe\xff')
        for address, size in ((0x7FFFFF, 1), (0x83FFFF, 2), (0x83FFFF, 16), (0x19C0000, 8)):
            with self.assertRaises(ValueError):
                flash.read(address, size)
        # Reads clear only the volatile W1C errors; never unlock or select erase.
        self.assertTrue(all(entry == (0xF7FE0000, 0xC000) for entry in memory.log))
        target = next(t for t in catalog() if t['key'] == 'spc58nh92')
        self.assertFalse(target['read_only'])
        self.assertIn('CFLASH', [r['name'] for r in target['regions']])

    def test_spc58_cflash_and_dflash_program_erase(self):
        memory = SimMemory(SPC58NH92)
        events = []
        flash = Flash(memory, SPC58NH92, progress=lambda p,d,t: events.append((p,d/t)))
        for address, size, selector, mask in ((0x800000, 0x10000, 0x3C, 1),
                (0xFC0000, 0x4000, 0x38, 0x80000),
                (0xFC4000, 0x4000, 0x38, 0x20000),
                (0x1000000, 0x20000, 0x40, 1),
                (0x1940000, 0x40000, 0x44, 0x40)):
            with self.subTest(address=hex(address)):
                flash.program(address, bytes(range(16)))
                self.assertEqual(flash.read(address, 16), bytes(range(16)))
                events.clear()
                flash.erase(address, size)
                self.assertEqual(flash.read(address, 16), b'\xff' * 16)
                self.assertIn((0xF7FE0000 + selector, mask), memory.log)
        before = len(memory.log)
        with self.assertRaisesRegex(ValueError, '16-byte'):
            flash.program(0x800000, b'12345678')
        self.assertEqual(len(memory.log), before)

    def test_spc58_core2_and_nexus_attributes(self):
        tap = Mock()
        once = OnCE(tap, SPC58NH92)
        with patch.object(once, 'select') as select, patch.object(once, 'write_register'), patch.object(once, 'wait_debug', return_value=0x209):
            once.attach()
        self.assertEqual({call.args[0] for call in select.call_args_list}, {0x28, 0x29, 0x2A})
        self.assertEqual(select.call_args_list[-1].args, (0x2A,))
        once = Mock(chip=SPC58NH92)
        once.nexus_read.side_effect = [1, 0x78563412]
        self.assertEqual(NexusMemory(once).read32(0x800000), 0x12345678)
        self.assertEqual(once.nexus_write.call_args_list[-1].args, (7, 0x90C00004))
        once.nexus_read.side_effect = [0]
        NexusMemory(once).write32(0xF4058000, 0xFF000002)
        self.assertEqual(once.nexus_write.call_args_list[-2].args, (7, 0xD0C00004))

    def test_spc58_rejects_mpc_id_before_reset(self):
        with patch('spc5tool.session.MpsseTransport.open') as opened, patch('spc5tool.session.JtagTap') as tap, patch('spc5tool.session.OnCE') as once:
            tap.return_value.read_idcode.return_value = 0x0988101D
            with self.assertRaisesRegex(DriverError, 'IDCODE mismatch'):
                with TargetSession(SPC58NH92):
                    pass
            once.assert_not_called()
            opened.return_value.close.assert_called_once()

    def test_spc58_uses_st_watchdog_without_mpc_peripheral_writes(self):
        values = {0xF4058000: 0xFF000003}
        with patch('spc5tool.session.MpsseTransport.open'), patch('spc5tool.session.JtagTap') as tap, patch('spc5tool.session.OnCE'), patch('spc5tool.session.NexusMemory') as memory:
            tap.return_value.read_idcode.return_value = 0x01146041
            memory.return_value.read32.side_effect = lambda a: values.get(a, 0)
            memory.return_value.write32.side_effect = lambda a, v: values.__setitem__(a, v)
            with TargetSession(SPC58NH92):
                self.assertEqual(values[0xF4058000], 0xFF000002)
            self.assertEqual([c.args[0] for c in memory.return_value.write32.call_args_list], [0xF4058010, 0xF4058010, 0xF4058000])


if __name__ == '__main__':
    unittest.main()
