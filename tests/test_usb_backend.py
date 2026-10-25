import contextlib
import io
import sys
import unittest
from types import ModuleType
from unittest.mock import Mock, patch

from spc5tool import usb_backend
from spc5tool.cli import main


class UsbBackendTests(unittest.TestCase):
    def test_native_backend_preserved_and_packaged_dll_used_when_missing(self):
        native, packaged = object(), object()
        package = ModuleType('libusb_package')
        package.get_libusb1_backend = Mock(return_value=packaged)
        with patch.dict(sys.modules, {'libusb_package':package}), \
             patch('usb.backend.libusb1.get_backend', return_value=native) as system:
            self.assertIs(usb_backend.get_backend(), native)
            package.get_libusb1_backend.assert_not_called()
            system.return_value = None
            self.assertIs(usb_backend.get_backend(), packaged)
            package.get_libusb1_backend.return_value = None
            self.assertIsNone(usb_backend.get_backend())

    def test_pyftdi_uses_registered_backend_and_registration_is_idempotent(self):
        from pyftdi.usbtools import UsbTools
        sentinel = object()
        with patch.object(UsbTools, 'BACKENDS', ('usb.backend.libusb1',)), \
             patch.object(usb_backend, 'get_backend', return_value=sentinel):
            usb_backend.configure_usb(); usb_backend.configure_usb()
            self.assertEqual(UsbTools.BACKENDS.count('spc5tool.usb_backend'), 1)
            self.assertIs(UsbTools.find_backend(), sentinel)

    def test_missing_library_explains_host_problem_and_preserves_other_errors(self):
        with patch.object(usb_backend.sys, 'platform', 'win32'):
            result = usb_backend.usb_error_message(ValueError('No backend available'))
        self.assertIn('Setup Windows.cmd', result)
        self.assertIn('MCU has not been queried', result)
        self.assertEqual(usb_backend.usb_error_message(OSError('access denied')), 'access denied')

    def test_backend_check_does_not_enumerate_or_open_hardware(self):
        with patch('pyftdi.usbtools.UsbTools.find_backend', return_value=object()), \
             patch('spc5tool.adapters.discover_tigards') as discover, \
             patch('spc5tool.mpsse.MpsseTransport.open') as opened, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['usb-check', '--backend-only']), 0)
            discover.assert_not_called(); opened.assert_not_called()
