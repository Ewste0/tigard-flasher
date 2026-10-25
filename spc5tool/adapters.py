"""Discover Tigard channel B using USB descriptors, without claiming interfaces."""
import re
from collections import Counter

from .mpsse import DEFAULT_URL
from .usb_backend import configure_usb


def discover_tigards():
    from pyftdi.ftdi import Ftdi
    from pyftdi.usbtools import UsbTools

    configure_usb()
    UsbTools.flush_cache()
    found = [(device, interface) for device, interface in Ftdi.list_devices(DEFAULT_URL)
             if interface == 2 and "tigard" in (device.description or "").lower()]
    serials = Counter(device.sn for device, _ in found)
    devices = []
    for device, interface in found:
        serial = device.sn or ""
        # Serial-specific URLs prevent a job from opening a different adapter.
        # Missing/duplicate serials cannot identify a Tigard unambiguously.
        # A numeric locator may be interpreted by pyftdi as a device index.
        selectable = bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", serial)) and serials[serial] == 1
        devices.append({"serial": serial, "description": device.description,
                        "interface": interface,
                        "url": f"ftdi://ftdi:2232h:{serial}/2" if selectable else None,
                        "bus": device.bus, "address": device.address})
    return sorted(devices, key=lambda device: (device["serial"], device["bus"] or 0,
                                              device["address"] or 0))
