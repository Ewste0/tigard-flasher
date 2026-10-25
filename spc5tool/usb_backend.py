"""PyFtdi backend provider with an installed libusb-package fallback."""
import sys
import threading

_lock = threading.RLock()


def get_backend():
    # Keep a working system libusb on macOS/Linux; otherwise load the wheel's
    # native library by its absolute path (including Windows DLLs).
    from usb.backend import libusb1
    with _lock:
        backend = libusb1.get_backend()
        if backend is not None:
            return backend
        try:
            from libusb_package import get_libusb1_backend
        except ImportError:
            return None
        return get_libusb1_backend()


def configure_usb():
    from pyftdi.usbtools import UsbTools
    with _lock:
        if __name__ not in UsbTools.BACKENDS:
            UsbTools.BACKENDS = (__name__, *UsbTools.BACKENDS)


def usb_error_message(error):
    original = str(error)
    if "no backend available" in original.lower():
        return ("USB library unavailable (No backend available). Install or repair libusb-package "
                "in the Python environment running this app, then restart it. "
                + ("On Windows, run Setup Windows.cmd, including USB driver setup. " if sys.platform == "win32" else "")
                + "The MCU has not been queried.")
    return original
