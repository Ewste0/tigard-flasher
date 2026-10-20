# Local release checks

Version 0.5.0, checked locally on macOS on 2026-10-20. Python 3.13.12,
Node.js 25.9.0, Puppeteer 25.14.0, Chrome 155.

| Check | Result |
| --- | --- |
| Python unit/API/driver tests | 98 passed |
| JTAG/SPI pin mapping checks | Passed |
| Browser wiring, presets, filenames, both reset mappings | Passed with mocked USB descriptors; no hardware jobs |
| OTP buttons, plan review, family password lengths, CFLASH preset region | Passed |
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
| Current USB detection | Selected Tigard absent; no target operation requested |

No OTP, SHADOW, password or SPC58 mutation was tested on real hardware.
Hardware evidence remains limited to the earlier MPC5748 configuration test in
[VALIDATION.md](VALIDATION.md). Device models are confined to `tests/` and are
not included in the installed application. Unsupported operations and known
limitations are listed in [SECURITY_OPERATIONS.md](SECURITY_OPERATIONS.md).

The previous revision passed hosted CI. Version 0.5.0 has local results only;
its GitHub Actions matrix must run after these changes are pushed.
