"""USB descriptor discovery never opens an FTDI interface."""
import unittest
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from spc5tool.adapters import discover_tigards
from spc5tool.mpsse import DEFAULT_URL
from spc5tool.web import Workspace
from tests.backends import tigard_descriptor


class DiscoveryTests(unittest.TestCase):
    def test_filters_other_ftdi_devices_and_returns_channel_b_serial_urls(self):
        modules = {name: ModuleType(name) for name in ('pyftdi', 'pyftdi.ftdi', 'pyftdi.usbtools')}
        def descriptor(serial, description, address):
            return SimpleNamespace(sn=serial, description=description, bus=1, address=address)
        listing = Mock(return_value=[
            (descriptor('OTHER', 'Dual RS232-HS', 1), 2),
            (descriptor('TEST-B', 'Tigard V1.2', 2), 2),
            (descriptor('TEST-B', 'Tigard V1.2', 2), 1),
            (descriptor('TEST-A', 'Tigard V1.1', 3), 2)])
        modules['pyftdi.ftdi'].Ftdi = SimpleNamespace(list_devices=listing)
        usb = modules['pyftdi.usbtools'].UsbTools = Mock(BACKENDS=("usb.backend.libusb1", "usb.backend.libusb0"))
        with patch.dict('sys.modules', modules):
            result = discover_tigards()
        self.assertEqual([device['serial'] for device in result], ['TEST-A', 'TEST-B'])
        self.assertEqual(result[1]['url'], 'ftdi://ftdi:2232h:TEST-B/2')
        self.assertEqual((result[1]['bus'], result[1]['address']), (1, 2))
        listing.assert_called_once_with(DEFAULT_URL)
        usb.flush_cache.assert_called_once_with()
        # All are visible, but ambiguous or malformed serials are not selectable.
        listing.return_value = [(descriptor(sn, 'Tigard', index), 2)
                                for index, sn in enumerate(('DUP', 'DUP', None, 'bad:/2', '1', '0x2'))]
        with patch.dict('sys.modules', modules):
            result = discover_tigards()
        self.assertEqual(len(result), 6)
        self.assertTrue(all(device['url'] is None for device in result))

    def test_backend_opens_the_reviewed_serial_even_if_workspace_preference_changes(self):
        descriptor = tigard_descriptor('REVIEWED')
        with tempfile.TemporaryDirectory() as directory, \
             patch('spc5tool.web.discover_tigards', return_value=[descriptor]):
            workspace = Workspace(directory)
            for target in ('spc560b64', 'w25q128jv'):
                plan = workspace.plan(dict(target=target, action='read', mode='hardware', address=0, length=8))
                workspace.url = tigard_descriptor('OTHER')['url']
                with patch('spc5tool.web.TargetSession') as session, patch('spc5tool.web.SpiNor.open') as spi:
                    with workspace._backend(plan, lambda *_: None):
                        pass
                opened = spi if target == 'w25q128jv' else session
                self.assertEqual(opened.call_args.args[1], descriptor['url'])
