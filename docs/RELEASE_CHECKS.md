# Local release checks

## 0.7.7 — 2026-10-25

| Check | Result |
| --- | --- |
| Python suite | 225 passed in the full run; the additional CLI regression passed in the final focused run (226 tests total) |
| Grouped erase | Nine regressions cover all six profiles, full/sparse selection masks, unchanged regions, stale selectors, locks, false-positive completion, USB failure, deadlines and progress |
| Ordinary Write | All-profile CFLASH/DFLASH preservation; three reads per changed block, two per unchanged block; backup/blank/verification failure checks |
| UI/API/CLI | Grouped erase plan, separate phase timings, backup and verified result; cancellation after mutation remains refused |
| Wiring and browser | Both reset mappings, presets, controls, S-records and scrolling passed |

No physical flash operation or new speed benchmark was performed. Tests count
controller starts and bytes transferred; they do not simulate silicon erase time.
See the grouped-erase evidence in [VALIDATION.md](VALIDATION.md).

## 0.7.6 — 2026-10-25

Checked on macOS with Python 3.13.12 and the installed Chrome. No test opened
an adapter or programmed physical flash. The earlier records below remain
historical; they do not expand current hardware qualification.

| Check | Result |
| --- | --- |
| Python unit/API/driver tests | 211 passed |
| New audit regressions | 26 tests across C90, C55, security, OTP policy and failure workflows; many profile/block subcases |
| C55 geometry/erase | All 38 MPC5748 CFLASH sectors; all 48 CFLASH and 4 DFLASH sectors for each SPC58 profile |
| Customer OTP and password policy | Reserved/range-crossing requests rejected before USB; all five exact C55 slots independently preserved and verified in controller tests |
| SHADOW | Reserved selector masks, interlock selection, public/private serial configurations and preservation tested on all three C90 profiles |
| Transport/image failures | Immediate stop on USB loss during cleanup; modified/truncated/extended reviewed images rejected before opening hardware |
| Browser | Both reset mappings, presets, cut 2.0 note, SPC58 customer OTP default, controls, S-record selection and 16 MiB scrolling passed |
| Wiring | JTAG/SPI mapping checks passed |
| Public source | Private-input and relative-link checks passed |

The comparison and unresolved physical/security qualification questions are in
[FEATURE_AUDIT.md](FEATURE_AUDIT.md). Hosted platform/build results are available
in [GitHub Actions](https://github.com/Ewste0/tigard-flasher/actions).

## Historical 0.7.1 checks

Version 0.7.1, checked locally on macOS on 2026-10-22. Python 3.13.12,
Node.js 25.9.0, Puppeteer 25.14.0, Chrome 155.

| Check | Result |
| --- | --- |
| Python unit/API/driver tests | 154 passed |
| Queued CFLASH read sequence, delayed readiness, stale-data rejection, bus/USB errors | Passed with independent clocked model; live results below |
| Queued default, Standard override, delay settings; API/CLI operation restrictions | Passed; browser tests start no hardware jobs |
| JTAG/SPI pin mapping checks | Passed |
| Browser wiring, presets, filenames, both reset mappings | Passed with mocked USB descriptors; no hardware jobs |
| OTP buttons, plan review, family password lengths, CFLASH preset region | Passed |
| MPC5748G 64-digit login input and API plan; SPC58 unavailable login controls | Passed in browser; no hardware jobs |
| C55 password scan, reset ordering, wide MPSSE transfer, failed-login cleanup | Passed offline |
| Customer Delivery reset-halt before password comparison; protected fallback and USB failures | Passed offline; buffer clearing verified on MPC5748G |
| JUN verification vs readable stored-password match; mismatches and protected regions | Passed offline; distinct result fields |
| Authentication-only sessions avoid unnecessary flash-buffer/watchdog setup | Passed offline and via live API |
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
| Updated original local UI | Seven targets; config default, TRST reset, 5 MHz |
| Quad-page programming and queued buffer writes | Offline byte-order, boundary, OTP exclusion, error/no-EHV tests passed |
| Hardware program-buffer check | 128 bytes in 0.003999 s; EHV never asserted; Config unchanged; 0.7.0 user full-write timing recorded below |
| 0.7.0 user full erase/write | 496.47 s erase + blank; 2115.30 s verified write; each includes backup |
| 0.7.1 controller transport | 64 buffer/control/discard cycles: 1.651 s previous, 0.336 s queued; no EHV; Config unchanged |
| 0.7.1 live full-image Verify | 6,225,920 bytes matched in 193.439 s, 5 MHz, no fallback; no erase/program |
| Optimized Write integrity | Three passes; all blocks backed up first; final full-block verify; outside-image corruption and false-positive erase rejected |
| Updated live defaults and verification | 5 MHz without override; Config Verify matched in 1.278 s; Write/Erase plans select queued access |
| Stop during Write/Erase/Program preflight | Backup and final checkpoint stop before modification; later cancellation rejected |
| Current USB detection | Tigard connected to MPC5748G, IDCODE 0x0988101D |
| Full queued CFLASH, 5 MHz / 10 µs | 236.556 s and 197.516 s; 6,225,920 bytes identical; no fallback |
| Standard comparison | Config plus 39 boundary/start/end samples match queued bytes |
| Updated live API default | Config reads at 1 MHz / 5 MHz automatically queued; 2.993 s / 1.033 s; match standard bytes |
| Earlier UTEST config/user read (0.5.1) | 16,224 bytes at 0x004000A0 succeeded at 1 MHz |
| Earlier MPC5748G password check (0.5.3) | Stored-password match; protected unlock explicitly unverified |
| Earlier Config read with and without password (0.5.3) | Two full 32 KiB reads matched byte-for-byte at 1 MHz |
| Earlier core state after USB session closed (0.5.3) | Primary halted, secondary cores held in reset |

No OTP, SHADOW, password or SPC58 mutation was performed during these checks.
The user previously installed a JTAG password and the app reported read-back
verification. Hardware evidence includes the MPC5748 configuration, UTEST reads,
and Customer Delivery password checks in
[VALIDATION.md](VALIDATION.md). Device models are confined to `tests/` and are
not included in the installed application. Unsupported operations and known
limitations are listed in [SECURITY_OPERATIONS.md](SECURITY_OPERATIONS.md).

Version 0.7.0 passed every hosted CI job. Version 0.7.1 has local results only;
its GitHub Actions matrix must run after these changes are pushed.
