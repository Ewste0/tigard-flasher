# Local release checks

Version 0.5.1, checked locally on macOS on 2026-10-20. Python 3.13.12,
Node.js 25.9.0, Puppeteer 25.14.0, Chrome 155.

| Check | Result |
| --- | --- |
| Python unit/API/driver tests | 105 passed |
| JTAG/SPI pin mapping checks | Passed |
| Browser wiring, presets, filenames, both reset mappings | Passed with mocked USB descriptors; no hardware jobs |
| OTP buttons, plan review, family password lengths, CFLASH preset region | Passed |
| Program once visibility | Visible for OTP only; CFLASH/DFLASH/SPI use Write |
| MPC5748 UTEST preset and factory ECC fault | Exact read ranges; errors retain physical address and MCR |
| OTP/password backup range regression | Exact changed units; API/CLI manifests verified; backup failure prevents programming |
| 16 MiB scrolling, exact bytes, partial rows, keyboard/wheel, file switching | Passed with generated data |
| OTP API read/program/verify/blank; repeat programming and erase rejection | Passed with controller doubles |
| SHADOW preservation and validation; password installation and secret cleanup | Passed with controller doubles |
| Operation progress through multiple blocks | Monotonic; failure cannot reach 100% |
| Missing serial-specific USB adapter | Disconnected JSON status; no empty HTTP response |
| Live-only defaults; disconnected hardware cannot produce a fake dump | Passed |
| Wheel and source distribution build; `twine check` | Passed |
| Installed-wheel CLI and seven bundled UI routes | Passed in temporary installation |
| Public source/distribution, relative links and private-input checks | Passed |
| Updated original local UI | Seven targets; config default, TRST reset, 1 MHz; idle |
| Current USB detection | Selected Tigard present; MPC5748G IDCODE confirmed |
| UTEST config/user read on hardware | 16,224 bytes at 0x004000A0 succeeded at 1 MHz |

No OTP, SHADOW, password or SPC58 mutation was tested on real hardware.
Hardware evidence includes the MPC5748 configuration and UTEST read tests in
[VALIDATION.md](VALIDATION.md). Device models are confined to `tests/` and are
not included in the installed application. Unsupported operations and known
limitations are listed in [SECURITY_OPERATIONS.md](SECURITY_OPERATIONS.md).

The previous revision passed hosted CI. Version 0.5.1 has local results only;
its GitHub Actions matrix must run after these changes are pushed.
