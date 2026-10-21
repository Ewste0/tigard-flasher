# Tigard Flasher feature audit

Reviewed 2026-10-21 for version 0.6.0. This audit lists implemented capabilities
and their validation limits. Hardware support is limited to the explicit device
profiles and documented regions.

| Category | Tigard Flasher 0.6.0 | Remaining limitation |
| --- | --- | --- |
| SPC/MPC over JTAG | Python TAP, OnCE/Nexus, and flash-controller drivers | Six explicit MCU profiles; additional devices require reviewed profiles and validation |
| Flash read/write | Read, patch write, program, verify, erase, blank check, backups | Hardware writes tested on one MPC5748 configuration block; UTEST config/user reads also tested |
| Queued CFLASH reads | Optional MPC5748G read batching with adjustable delay and per-word status checks | Offline tests only; physical speed and three-minute full reads unverified |
| OTP / SHADOW | OTP read/program/verify/blank; SHADOW preservation | OTP cannot erase; BAF and HSM programming unavailable |
| Passwords | Family lengths, SPC560/MPC5748G password entry, password installation | MPC5748G stored-password comparison verified in Customer Delivery; protected-target unlock unvalidated; SPC58 login and PASS-group unlock unavailable; no automatic protection activation |
| SPI flash | W25Q128JV / EF4018 profile with offline tests | Other SPI chips not supported; no SPI bench test |
| Read presets | MPC5748 config/full and UTEST config/user presets; SPC58NH DFLASH plus explicit regions | Presets are device-specific; a selected range is not necessarily a complete raw dump |
| Work with dumps | Load/save, SHA-256, hex/ASCII scrolling, range navigation | Viewer, not an in-place hex editor |
| Tigard adapter | FT2232H channel B via pyftdi/libusb | macOS bench test; other host USB setups untested |
| Desktop workflow | Browser UI served locally, CLI, source launchers | No signed native installer |

The independently implemented UI uses Tigard Flasher branding. The application
runs locally without a subscription or third-party account.

See [VALIDATION.md](VALIDATION.md) for evidence and [WIRING.md](WIRING.md) for the
supported connector references. Adding a profile requires reviewed geometry,
offline failure tests, and separate hardware validation.
