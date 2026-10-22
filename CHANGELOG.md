# Changelog

## 0.7.0 — 2026-10-22

- Default the interface clock to 5 MHz across UI, CLI and hardware sessions.
  Explicit clock overrides remain available; API plans inherit the server clock.
- Extend queued MPC5748 CFLASH access to Write/Erase backups, blank checks and
  verification, removing the previous polling-only bottleneck in these steps.
- Program complete aligned 128-byte MPC5748 CFLASH quad-pages in one pulse,
  with queued buffer loading and per-word status checks before EHV. Partial pages,
  all-ones ECC units, OTP and other families keep the original programming path.
  USB failures stop without committing the buffer; no speculative write retries.
- Allow Stop during backups and preflight, atomically disabling cancellation
  before flash modification. Read, Verify and Blank check are also cancellable.
- Verify a queued 128-byte buffer transfer on hardware without an EHV pulse;
  Config remained unchanged. Actual erase/write timing awaits a selected image.
  Record total operation duration. No PEmicro-equivalent performance claim.

## 0.6.1 — 2026-10-22

- Make queued reading the default for MPC5748G CFLASH Read in the UI, API and
  CLI at 100 kHz or faster. Keep the 10 µs delay, per-word status checks and
  automatic polling fallback. Other regions and operations retain standard access.
- Add `--standard-read` for the CLI; preserve the UI Standard setting and API
  `fast_read: false` override. Remove the experimental label from queued mode.
- Verify queued data on the connected MPC5748G against standard Config reads,
  standard samples across every CFLASH block boundary and a repeated full dump.
  Full reads take about four minutes at 5 MHz on this board.
- Add default/override coverage for the UI, API and CLI, including other chips,
  UTEST, low clocks and non-read operations.

## 0.6.0 — 2026-10-21

- Add opt-in queued MPC5748G CFLASH reads in the UI, API and CLI, with an adjustable
  1–1000 µs settling delay (default 10 µs). Standard reads remain the default.
- Queue 64 single-word reads per USB exchange. Capture ready/error status before
  each data word; discard a premature batch and continue with polling. Bus errors
  retain the exact address. Writes, OTP and other families retain standard access.
- Display read time and throughput, including whether polling fallback occurred.
- Add 12 clocked-model regressions for exact data, alignment, delayed completion,
  bus/USB errors, bounded replies and operation restrictions. All 132 tests and
  browser checks pass. Tigard was disconnected; physical throughput is unmeasured.

## 0.5.3 — 2026-10-20

- Fix MPC5748G password-assisted flash setup on Customer Delivery devices:
  halt under reset, then compare the installed password without running firmware.
  The current-password field can remain populated for normal memory operations.
- Retain the C55 password scan for protected devices; do not retry after a USB
  failure or treat a pre-existing unlock flag as proof of a supplied password.
- Report buffer setup failures with the register address and before/after values.
- Add five regression tests covering the observed buffer failure, fallback
  behavior, cleanup and password verification.
- All 120 Python tests and browser checks pass. On the connected MPC5748G,
  two 32 KiB Config reads, with and without the password, matched exactly.
  Core halt/reset state persisted after the USB session closed.

## 0.5.2 — 2026-10-20

- Enable MPC5748G's 64-digit current-password input in the UI, API and CLI.
- Submit a 256-bit JTAG password after reset release and inspect PASS status.
  Distinguish hardware-confirmed unlock from comparing the readable password
  on an already-open Customer Delivery device. Protected-target unlocking
  remains bench unvalidated.
- Reject mismatching stored passwords before watchdog and flash setup, without
  exposing the credential in error messages, plans, status or logs.
- Clarify that installation verifies storage and does not activate protection.
  Disable unused login inputs on families without a login implementation.
- Keep authentication-only sessions independent of flash-buffer/watchdog setup.
- Add ten regression tests for scan ordering, MPSSE transport, error handling,
  status verification, API/CLI results and credential privacy, plus browser checks.
  All 115 Python tests pass. The live UI API checked the user's installed password;
  protected-target unlock was explicitly not reported as verified.

## 0.5.1 — 2026-10-20

- Fix MPC5748G UTEST selection: default to config/user bytes at 0x004000A0,
  excluding factory ECC tests; add a separate 12 KiB user OTP preset.
- Report the exact Nexus failure address, direction, and flash ECC status.
- Back up only affected OTP units before programming or installing a password,
  so unrelated factory ECC patterns cannot abort the backup.
- Show Program once only for OTP regions; ordinary flash uses Write.
- Add seven regressions for ECC failures and OTP backup contents/order, plus
  browser checks. All 105 Python tests and browser checks pass locally.
- Two reads of the 16,224-byte UTEST config/user range matched on one MPC5748G.
  No physical OTP or password programming was performed.

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
