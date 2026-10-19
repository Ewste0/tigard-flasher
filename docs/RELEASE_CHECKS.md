# Local release checks

Version 0.4.0, checked locally on macOS on 2026-10-19. Python 3.13.12,
Node.js 25.9.0, Puppeteer 25.14.0, Chrome 155.

| Check | Result |
| --- | --- |
| Python unit/API/driver tests | 84 passed |
| JTAG/SPI pin mapping checks | Passed |
| Browser wiring, presets, filenames, both reset mappings | Passed with mocked USB descriptors; no hardware jobs |
| 16 MiB scrolling, byte accuracy, final partial row, wheel/keyboard, file switching | Passed with generated data |
| Live-only defaults and rejected simulation/demo routes | Passed |
| Disconnected hardware cannot produce a fake dump | Passed |
| Wheel and source distribution build | Passed |
| Package metadata and README validation (`twine check`) | Passed |
| Installed-wheel CLI and seven bundled UI routes | Passed in a temporary installation |
| Public-source, distribution, relative-link and private-input checks | Passed |
| Local running UI | Detected Tigard; config-only 32 KiB, TRST reset, 1 MHz; no read/write requested |

The installed wheel contains no device simulator. Test doubles live under
`tests/` in the source release. Hardware programming evidence is limited to the
earlier MPC5748 configuration test described in [VALIDATION.md](VALIDATION.md).

GitHub Actions is configured for Linux/macOS/Windows and Python 3.10/3.13, but
hosted CI has not run during local preparation. No repository or release has
been published by this preparation step.
