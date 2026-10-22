"""Exercise hardware API calls with fake pyftdi modules; never enumerate USB."""
import sys
import types
import unittest
import tempfile
from unittest.mock import Mock, call, patch

from spc5tool.chips import SPC560B64, SPC5748G
from spc5tool.errors import DriverError
from spc5tool.mpsse import MpsseTransport, MpsseError
from spc5tool.session import TargetSession
from spc5tool.spi import PARTS, SpiNor
from spc5tool.web import Workspace


class HardwareContractTests(unittest.TestCase):
    def fake_modules(self):
        parent, ftdi, spi = (types.ModuleType(n) for n in ('pyftdi', 'pyftdi.ftdi', 'pyftdi.spi'))
        ftdi.Ftdi = Mock()
        spi.SpiController = Mock()
        usbtools = types.ModuleType('pyftdi.usbtools')
        usbtools.UsbTools = Mock()
        return {'pyftdi': parent, 'pyftdi.ftdi': ftdi, 'pyftdi.spi': spi,
                'pyftdi.usbtools': usbtools}, ftdi.Ftdi, spi.SpiController

    def test_jtag_channel_pin_direction_frequency_and_close(self):
        modules, factory, _ = self.fake_modules()
        with patch.dict(sys.modules, modules):
            with MpsseTransport.open(freq=100000) as transport:
                factory.return_value.open_mpsse_from_url.assert_called_once_with(
                    'ftdi://ftdi:2232h/2', direction=0x3B, initial=0x38, frequency=100000)
            factory.return_value.close.assert_called_once()
            modules['pyftdi.usbtools'].UsbTools.flush_cache.assert_called_once()

    def test_absent_serial_adapter_returns_disconnected_status(self):
        modules, factory, _ = self.fake_modules()
        factory.list_devices.side_effect = RuntimeError('No USB device with selected serial')
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, modules):
            state = Workspace(directory).adapter()
        self.assertFalse(state['connected'])
        self.assertEqual(state['devices'], [])
        self.assertIn('No USB device', state['message'])
        factory.return_value.open_mpsse_from_url.assert_not_called()

    def test_jtag_failed_open_closes(self):
        modules, factory, _ = self.fake_modules()
        factory.return_value.open_mpsse_from_url.side_effect = OSError('disconnected')
        with patch.dict(sys.modules, modules), self.assertRaisesRegex(MpsseError, 'disconnected'):
            MpsseTransport.open()
        factory.return_value.close.assert_called_once()

    def test_spi_cs_channel_mode_and_close(self):
        modules, _, factory = self.fake_modules()
        factory.return_value.get_port.return_value.exchange.return_value = bytes.fromhex('ef4018')
        with patch.dict(sys.modules, modules):
            flash = SpiNor.open(PARTS['w25q128jv'])
            factory.assert_called_once_with(cs_count=1)
            factory.return_value.configure.assert_called_once_with('ftdi://ftdi:2232h/2')
            factory.return_value.get_port.assert_called_once_with(cs=0, freq=5000000, mode=0)
            flash.close()
            flash.close()
        factory.return_value.terminate.assert_called_once()

    def test_spi_wrong_identity_closes_before_write(self):
        modules, _, factory = self.fake_modules()
        port = factory.return_value.get_port.return_value
        port.exchange.return_value = b'\xff\xff\xff'
        with patch.dict(sys.modules, modules), self.assertRaisesRegex(DriverError, 'JEDEC'):
            SpiNor.open(PARTS['w25q128jv'])
        factory.return_value.terminate.assert_called_once()
        port.exchange.assert_called_once_with(b'\x9f', 3)

    def test_session_id_mismatch_prevents_reset(self):
        with patch('spc5tool.session.MpsseTransport.open') as opened, patch('spc5tool.session.JtagTap') as tap, patch('spc5tool.session.OnCE') as once:
            tap.return_value.read_idcode.return_value = 0x1234501D
            with self.assertRaisesRegex(DriverError, 'mismatch'):
                with TargetSession(SPC560B64, expected_id=0x2234501D):
                    pass
            once.assert_not_called()
            opened.return_value.close.assert_called_once()

    def test_session_c55_initialization_and_close(self):
        values = {0xFC050000: 0xFF00000B, 0xFC054000: 0xFF00000B, 0xFC058000: 0xFF00000B,
                  0xFC030000: 0x123456FF, 0xFC030004: 0x123456FF, 0xFC030018: 0x123456FF}
        with patch('spc5tool.session.MpsseTransport.open') as opened, patch('spc5tool.session.JtagTap') as tap, patch('spc5tool.session.OnCE') as once, patch('spc5tool.session.NexusMemory') as memory:
            tap.return_value.read_idcode.return_value = 0x1234501D
            memory.return_value.read32.side_effect = lambda a: values.get(a, 0)
            memory.return_value.write32.side_effect = lambda a, v: values.__setitem__(a, v)
            with TargetSession(SPC5748G, expected_id=0x1234501D):
                self.assertEqual(values[0xFC050000], 0xFF00000A)
                self.assertEqual(values[0xFC030000], 0x123456AE)
            once.return_value.attach.assert_called_once()
            opened.return_value.close.assert_called_once()

    def test_session_attach_failure_closes(self):
        with patch('spc5tool.session.MpsseTransport.open') as opened, patch('spc5tool.session.JtagTap') as tap, patch('spc5tool.session.OnCE') as once:
            tap.return_value.read_idcode.return_value = 0x1234501D
            once.return_value.attach.side_effect = DriverError('cannot halt')
            with self.assertRaisesRegex(DriverError, 'cannot halt'):
                with TargetSession(SPC560B64):
                    pass
            opened.return_value.close.assert_called_once()

    def test_stuck_buffer_reports_register_and_stops_preparation(self):
        with patch('spc5tool.session.MpsseTransport.open') as opened, \
             patch('spc5tool.session.JtagTap') as tap, \
             patch('spc5tool.session.OnCE'), \
             patch('spc5tool.session.NexusMemory') as memory:
            tap.return_value.read_idcode.return_value = 0x0988101D
            memory.return_value.read32.side_effect = lambda a: 0x00432407 if a == 0xFC030000 else 0
            with self.assertRaisesRegex(DriverError, '0xFC030000.*after=0x00432407.*before any erase'):
                with TargetSession(SPC5748G):
                    self.fail('buffer preparation must not be ignored')
            self.assertNotIn(call(0xFC030004, 0), memory.return_value.write32.call_args_list)
            opened.return_value.close.assert_called_once()

    def test_session_passes_selected_reset_lead(self):
        with patch('spc5tool.session.MpsseTransport.open') as opened, patch('spc5tool.session.JtagTap') as tap:
            tap.return_value.read_idcode.return_value = 0
            with self.assertRaises(DriverError):
                with TargetSession(SPC5748G, reset_pin='trst'):
                    pass
            opened.assert_called_once_with('ftdi://ftdi:2232h/2', 5000000, reset_pin='trst')


if __name__ == '__main__':
    unittest.main()
