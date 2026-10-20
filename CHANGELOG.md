# Changelog

## 0.5.0 — 2026-10-20

- Add SPC560B54, SPC560B60, and SPC58EH92 profiles.
- Enable SPC58H CFLASH/DFLASH program and erase with 16-byte program units.
- Add SHADOW, CFLASH_OTP, DFLASH_OTP and UTEST operations with per-region capabilities.
- Add password length validation, SPC560 held-reset authentication, and password installation.
- Preserve SHADOW boot/security configuration during erase and validate rebuilt images.
- Fix operation labels and continuous progress through backup, erase, program and verify.
- Keep C55 login and HSM programming unavailable pending verified implementations.
- New functionality has offline coverage only; no new hardware qualification is claimed.

## 0.4.0 — 2026-10-19

- First standalone public source package with portable launchers and distribution metadata.
- SPC/MPC JTAG drivers, W25Q128JV SPI support, with device models isolated in automated tests.
- MPC5748 config/full presets; SPC58NH92 DFLASH-only reading; SPC560B64 profile.
- Continuous hex/ASCII viewer, named saves, USB status, interactive wiring guide.
- Backups, mutation plans, ID interlocks, preservation, verification, and canceled-read handling.
- Live-only UI and CLI: no demo generator, simulation selector, or hardware fallback.
- Limited MPC5748 configuration bench evidence documented separately from offline coverage.
- Python/browser CI, package checks, release tooling, and privacy exclusions.
