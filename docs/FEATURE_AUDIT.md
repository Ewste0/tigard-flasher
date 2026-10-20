# SKADI feature comparison

Reviewed 2026-10-20 against the public
[SKADI Flasher product page](https://www.skadiauto.com/products/flasher).
This is a comparison of advertised categories, not a certification of equivalent
algorithms, supported chips, performance, or reliability. The public description
does not provide an exhaustive device list or a test specification.

| Advertised category | Tigard Flasher 0.5.0 | Remaining limitation |
| --- | --- | --- |
| SPC/MPC over JTAG | Python TAP, OnCE/Nexus, and flash-controller drivers | Six explicit MCU profiles; no exhaustive SKADI device list available |
| Flash read/write | Read, patch write, program, verify, erase, blank check, backups | Hardware proof is restricted to one MPC5748 configuration block |
| OTP / SHADOW | OTP read/program/verify/blank; SHADOW preservation | OTP cannot erase; BAF and HSM programming unavailable |
| Passwords | Family lengths, SPC560 login, password installation | C55 JTAG login and PASS-group unlock await verified protocol details; no automatic protection activation |
| SPI flash | W25Q128JV / EF4018 profile with offline tests | Other SPI chips not supported; no SPI bench test |
| Ready-made presets | MPC5748 config/full and SPC58NH DFLASH plus explicit regions | No claim of matching SKADI's entire preset catalogue |
| Work with dumps | Load/save, SHA-256, hex/ASCII scrolling, range navigation | Viewer, not an in-place hex editor |
| Tigard adapter | FT2232H channel B via pyftdi/libusb | macOS bench test; other host USB setups untested |
| Desktop workflow | Browser UI served locally, CLI, source launchers | No signed native installer; no SKADI binary or protocol dependency |

Full feature or hardware parity with SKADI has not been established.
The UI is independently implemented and uses its own branding. No subscription
service, third-party account integration, or licence-check mechanism is included.

See [VALIDATION.md](VALIDATION.md) for evidence and [WIRING.md](WIRING.md) for the
supported connector references. Adding a profile requires reviewed geometry,
offline failure tests, and separate hardware validation.
