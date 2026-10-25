# Windows setup and USB driver

For Windows 10/11 x64, extract the source release into a permanent writable
folder and double-click **Setup Windows.cmd**. Connect exactly one Tigard and
close applications using its serial/JTAG ports first.

Setup finds Python 3.10+ or installs Python 3.13 through Windows Package Manager
when available, creates `.venv`, installs the app and its native USB library,
and downloads the versioned driver helper from this project's GitHub release.
It checks the archive SHA-256 against the release manifest before running it.
The helper selects a Tigard composite parent with USB ID `0403:6010` and installs
**libusb-win32** after the Windows administrator/UAC prompt. It refuses to select
multiple Tigards or proceed while another FT2232H parent is connected. A canceled
or failed installation is reported as a failure; it is never reported as ready.

The driver replaces the adapter's FTDI COM-port driver. Its two COM ports may
disappear; return to the FTDI driver in Device Manager if you need UART/VCP use.
The installed driver package matches FT2232H hardware IDs, so Windows may also
offer it for other adapters using the same IDs. No USB class-wide filter is installed.

Setup creates a desktop shortcut and opens the flasher after the USB descriptor
check passes. It never reads IDCODE, clocks JTAG, resets the MCU, or writes target
memory. **Identify** is a separate user action. The application itself runs
without administrator privileges.

Python/driver downloads require internet during initial setup. If `winget` is
unavailable and Python is absent, install Python from
[python.org](https://www.python.org/downloads/windows/) and rerun setup.
Keep the extracted folder: the environment and desktop shortcut use its files.
To update a Git checkout, pull changes and rerun setup.

`Setup Windows.cmd -SkipDriver` installs just the application and USB library.
`-NoLaunch` finishes without opening the application. Other Windows architectures
can use manual Python installation and Zadig; automatic driver setup currently
targets x64 Windows only.

## Diagnose an error

Run these in the application's folder:

```bat
.venv\Scripts\python -m spc5tool usb-check --backend-only
.venv\Scripts\python -m spc5tool usb-check
```

The first loads the USB library without accessing any USB device. The second
lists Tigard descriptors, without claiming a JTAG/SPI interface.

| Result | Meaning / next step |
| --- | --- |
| `No backend available` | Host libusb could not load. Rerun setup in this app's environment; the MCU has not been queried. |
| COM ports appear, but no Tigard in the app | Windows enumerated USB; check the device-driver installation. COM detection does not validate JTAG. |
| USB library loads, no adapter listed | Check USB connection/driver and reconnect the adapter. |
| Access denied / device busy | Close other tools using Tigard and check the USB driver. |
| IDCODE `0xFFFFFFFF` or `0x00000000` | USB opened, but JTAG identification failed. Check target connection, mode, reference voltage and power. |

Manual driver installation follows
[PyFtdi's Windows guide](https://eblot.github.io/pyftdi/installation.html#windows):
use [Zadig](https://zadig.akeo.ie/), show all devices and composite parents,
select the Tigard parent (without `Interface 0/1`), then select **libusb-win32**.
Restart the flasher after changing the driver. The native libusb library and
the Windows device driver are separate requirements.

## Build and validation

The helper source is [tigard_usb_driver.c](../tools/windows/tigard_usb_driver.c).
[build_driver.ps1](../tools/windows/build_driver.ps1) builds it with Visual Studio
2022 and pinned SHA-256 inputs: [libwdi 1.5.1](https://github.com/pbatard/libwdi/tree/v1.5.1),
[libusb-win32 1.4.0.2](https://github.com/mcuee/libusb-win32/tree/release_1.4.0.2),
and [libusbK 3.1.0.0](https://github.com/mcuee/libusbk/tree/V3.1.0.0).
Their source and licenses are available at those links. The helper archive includes
libwdi source, LGPL/GPL notices, our helper source and its reproducible build recipe.
Rebuilding a modified libwdi/helper is supported. Native driver files remain
upstream binaries with their original signatures; the installation uses libwdi's
catalog-generation flow. Windows security-policy failures are reported rather
than disabling signature enforcement or Secure Boot.

CI compiles the Windows helper and loads the USB library on Windows. Separate
tests exercise the helper's selection and failure paths using fake libwdi calls.
Actual driver installation and JTAG hardware behavior on Windows still need a
physical Windows/Tigard test; passing CI is not hardware validation.
