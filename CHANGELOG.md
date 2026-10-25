# Changelog

## 0.7.5 — 2026-10-25

- Align SPC560 debug entry with the inspected P&E connection order: arm
  DR/WKUP/FDB and initialize DBCR0 twice while RESET is asserted. Reinitialize
  after the confirmed halt, then clear the debug request before stepping.
- Submit SPC560 CPUSCR and GO together in one 192-bit update, with EX clear;
  issue exactly one instruction and never replay after a USB failure.
- Tag injected instructions as VLE on SPC560 as well as MMU-equipped cores.
  The previous MMU-dependent condition sent CTL=0x400 instead of 0x402 on
  SPC560, despite submitting a VLE opcode. Follow ST AN4035's instruction-format
  handling independently of memory translation.
- When a new step reports ERR while halted and clocked, capture PC/IR/MSR/CTL
  and OCR/DBCR0/DBSR using read-only debug scans. Exclude WBBR operands, never
  replay the instruction, and stop diagnostic I/O on a transport failure.
- Add wire-format, stale-result and diagnostic failure/privacy regressions.
  Record the limits of the P&E and SEGGER static comparisons.
  The user reports P&E Micro can access the same target; our Windows SPC560
  target still needs a retest. This release does not claim hardware success.

## 0.7.4 — 2026-10-25

- Remove the SPC560 first-step dependency on uninitialized SRAM. Inject VLE
  instructions at the documented BAM ROM address, with the following fetch
  also inside ROM. Host-driven memory/flash access no longer modifies scratch RAM.
- Check instruction execution with a register-only self-test before memory
  access. It requires a transformed result, rejects stale scans, and preserves
  strict error handling with no automatic instruction replay.
- Initialize e200z0h DBCR0 with the documented second write after enabling EDM.
- Add regression coverage for early instruction fetch, empty SRAM, register
  self-test failure and session cleanup. The reported SPC560B64L3 Windows target
  still needs a hardware retest; offline tests do not establish successful access.

## 0.7.3 — 2026-10-25

- Import Motorola S-record files in the UI and CLI, validating checksums, record
  counts, addresses and overlaps. Show decoded bytes at MCU addresses; choose
  sparse segments explicitly without padding gaps or relocating data.
- Preserve original S-record downloads. Write, Verify and Program once use the
  selected segment, with existing backup, alignment and OTP protections.
- Correct SPC560/e200z0h debug entry handling for initial `OSR=0x309`:
  DEBUG is already set; ERR describes the previous single-step. Initial entry
  accepts that state, while every new instruction still fails on ERR. Checkstop,
  reset and invalid scans remain guarded; instruction failures show opcode/PC.
- This debug-entry correction has offline regression coverage; successful reading
  of the reported SPC560B64L3 on Windows still requires a hardware retest.
- Windows setup reuses the verified v0.7.2 USB helper release.

## 0.7.2 — 2026-10-25

- Detect newly connected Tigards every three seconds while the UI is idle.
  Select the only connected adapter automatically, with a connection-settings
  selector for multiple adapters and an explicit choice that stays pinned.
- Bind each operation review to its adapter. Disconnects, USB re-enumeration,
  and adapter changes invalidate pending plans and discard their passwords;
  the UI clears the previous target ID/password fields. Running operations
  keep their serial-specific connection and are never redirected.
- Test discovery filtering, reconnects, multiple adapters, stale reviews,
  active-operation isolation, and browser updates without physical USB access.
- Load the libusb-package native library when system libusb is unavailable;
  explain missing USB backends separately from target communication failures.
- Add Windows x64 setup for Python, dependencies, desktop shortcut and the
  Tigard libusb-win32 driver, plus a USB-only diagnostic command. Build the
  narrow driver helper from pinned upstream inputs; Windows hardware validation
  remains pending.

## 0.7.1 — 2026-10-22

- Reduce MPC5748 queued Write from five read passes per changed block to three:
  backup, erase blank check, and final verification of the complete rebuilt block.
  Preserve out-of-image bytes, skip unchanged blocks, and keep OTP/SHADOW paths separate.
- Send each MPC5748 flash-controller access in one USB exchange, checking Nexus
  completion before the next controller command. Delayed writes poll without
  replay; programming still waits for flash DONE and checks PEG/errors.
- Stop bus cleanup after an indeterminate USB failure. Add failure tests for
  blank checks, preserved-byte corruption, backup errors and delayed replies.
- Report separate backup, erase/blank, program and verify times in the UI log/API.
- Hardware: 64 discarded buffer/control cycles fell from 1.651 s to 0.336 s;
  no EHV pulse was issued and Config was unchanged. This is a transport timing,
  not an end-to-end programming measurement for 0.7.1. Full-image Verify through
  the updated app matched all 6,225,920 bytes in 193.439 s without fallback.
- Record the user's successful 0.7.0 full CFLASH erase (496.47 s) and verified
  write (2115.30 s), both including backups, on the existing MPC5748G bench.

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
