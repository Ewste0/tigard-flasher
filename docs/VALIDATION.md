# Validation and scope

## SPC560 first instruction follow-up — 2026-10-25

On v0.7.3 the user's Windows SPC560B64L3 reached the first injected instruction,
then reported `single-step 0x181F0900, next PC 0x40000000: OSR=0x309`.
This is a new execution failure, so the driver correctly stopped before flash
access. It is not evidence of a bad password or a disconnected USB adapter.

The previous bootstrap put the injected PC at `0x3FFFFFFC`, outside SRAM, and
directed the next instruction fetch into SRAM while that same instruction was
initializing its ECC. That makes initialization depend on fetch/store ordering.
The status alone does not establish which event caused the reported error.
[NXP AN4365 sections 2.6–2.7 and 4.2](https://www.nxp.com/docs/en/application-note/AN4365.pdf)
describe the next fetch and the ECC initialization requirement.

Version 0.7.4 removes this dependency: the host-driven SPC560 backend needs no
RAM loader or scratch buffer. Its injected PC is `0xFFFFC000` and the following
fetch is `0xFFFFC004`, both inside the 16 KiB BAM ROM documented in
[MPC5607B RM Table 3-1 and section 5.2](https://community.nxp.com/pwmxy87654/attachments/pwmxy87654/mpc5xxx/15469/1/MPC5607B_Reference_Manual.pdf).
Only the injected instruction executes; GO does not include EX to resume ROM
firmware. Before any memory access, two `e_ori r1,r31,1` operations must return
their transformed operands in WBBR. This checks execution rather than accepting
an echo of staged input. All new steps still reject ERR, with no retry. The z0h
attach path also performs the second DBCR0 write required to initialize other
fields after setting EDM, per
[ST RM0046 section 36.14](https://www.st.com/resource/en/reference_manual/rm0046-spc560p34spc560p40-32bit-mcu-family-built-on-the-embedded-power-architecture-stmicroelectronics.pdf).

Offline coverage now resolves instruction fetch before store effects, reproduces
the old bootstrap failure, and exercises the real MPSSE/TAP/OnCE path with empty
SRAM. Failed or stale self-tests must close the session before flash setup.
GNU PowerPC disassembly independently confirmed the injected VLE opcodes.
No physical target was accessed for this update. Successful SPC560 reading,
writing and erasing still require hardware validation; this change addresses a
bootstrap defect, not a proven diagnosis of every `OSR=0x309` failure.

## SPC560 debug entry and S-record input — 2026-10-25

The reported `core 0 (TAP 0x11), OSR=0x309` has MCLK, ERR and DEBUG set,
with RESET and CHKSTOP clear. It is a valid OnCE status, not an all-ones scan.
[ST RM0046, e200z0h Table 467 and section 36.14](https://www.st.com/resource/en/reference_manual/rm0046-spc560p34spc560p40-32bit-mcu-family-built-on-the-embedded-power-architecture-stmicroelectronics.pdf)
defines ERR as the result of the last single-stepped instruction. The documented
entry sequence checks debug mode before initializing debug registers; new steps
must then re-enter DEBUG without ERR. This core protocol also agrees with
[NXP AN4365 sections 2.3–2.7](https://www.nxp.com/docs/en/application-note/AN4365.pdf).

The driver now permits that initial ERR state on the z0h attach path only. It
does not clear a nonexistent writable OSR flag, retry failed memory instructions,
or treat a failed step as data. Every subsequent instruction still requires
ERR clear; errors include its opcode and next PC. Invalid scans, reset and
checkstop remain rejected or time out. The screenshot establishes the initial
status; its underlying cause and successful reading after this change have not
been verified on the user's Windows target.

Synthetic tests cover this entry/step distinction and S-record checksums,
counts, mixed S1/S2/S3 records, missing optional headers/terminators, overlaps,
overflow, sparse segment selection, original downloads, address guards and OTP
operations. Four private SPC560 dump files were decoded and independently
compared locally: CFLASH 0x00000000 / 0x180000 bytes; DFLASH 0x00800000 /
0x10000; CFLASH_OTP 0x00400000 / 0x2000; DFLASH_OTP 0x00C00000 / 0x2000.
No USB access or target writes were performed for these input checks; none of
the private dump contents is included in the repository.

## Hardware bench record — 2026-10-19

Host: macOS, Python 3.13, Tigard v1.1. Target: one MPC5748G board,
IDCODE `0x0988101D`. Wiring: TRST to RESET, SRST to JCOMP.

- IDCODE and reset/halt attach succeeded. Core 0 halted; cores 1/2 were held in
  reset with their module clocks inactive. This is one observed boot configuration.
- Two 256-byte reads at `0x01000000` agreed; a later 16 KiB read began with the same bytes.
- Two 32 KiB configuration reads at `0x00FA8000` agreed.
- A requested one-byte change at the start of that configuration range was tested.
  Both 16 KiB blocks were backed up before erase. The first block was erased and
  reconstructed, preserving the remaining bytes; the complete image was verified.
- A fresh connection and a separate 32 KiB read matched the input image byte for byte.
  Original-block backup content matched the pre-write read.
- At 1 MHz, the write took approximately 154 seconds; a 32 KiB read took 26 seconds.

Private dumps, test input, backup ZIPs, identifiers, and raw session logs are
intentionally excluded from this public repository. These are maintainer-reported
bench observations, not independently reproducible public firmware fixtures.
Firmware execution and boot behavior after the test were not evaluated.

At this initial check the full main-array read was canceled. Later full-array
read/write results appear below. Other MPC5748 boards, all SPC560/SPC58 variants,
and SPI remain unvalidated on hardware. The CLI's `hardware_validated: false` means the entire profile is not
qualified; it does not discard the limited MPC5748 bench observations above.

## UTEST bench follow-up — 2026-10-20

On the same MPC5748G/Tigard setup at 1 MHz, the full UTEST read failed at
`0x00400060` with `RWCS=0x10000006`. FMC MCR changed from `0x00000600` to
`0x00008600`, setting the ECC error flag. UID, DCF, user OTP, and configuration
sample reads succeeded. A contiguous read of all 16,224 bytes from
`0x004000A0` through `0x00403FFF` succeeded. A second read through the updated
UI API saved a dump with the same SHA-256 in approximately 13 seconds.
This range excludes the factory
ECC tests and is not a complete raw UTEST image.

The initially reported password-install job failed while backing up the entire UTEST
block, before a programming pulse. Version 0.5.1 backs up only the affected
OTP units (32 bytes for a C55 password). That change has failure-injection and
end-to-end API/CLI tests. Subsequently, the user ran an installation into the
32-byte JTAG slot; the app's completed job reported read-back verification.
The MPC5748 read presets are not applied to SPC58H's different factory map.

## Password-login follow-up — 2026-10-20

The MPC5748G profile previously allowed installing a password while rejecting all
password-login requests. Version 0.5.2 enables 64-digit entry and a 256-bit JTAG
scan after external reset release, with explicit result verification.

On the same board, the stored password remained readable after the user cycled
power. Ordinary reset/halt worked without a password. PASS_LCSTAT was
`0xA0000003` (Customer Delivery, JUN clear), and remained unchanged after
password submission. The 16 PASS lock registers were unchanged. All cores were
halted or held in reset. No protection activation, lifecycle advancement, DCF
programming, or flash programming was performed during these login checks.
This establishes accessible debug and a stored-password comparison, not
successful authentication against an enforced debug lock. Protected-target
unlock remains unvalidated.

The live UI API subsequently accepted the user's full 64-digit password and
completed with `method: stored_password`, `password_verified: true`, and
`unlock_verified: false`. Plans and status were checked for credential leakage.
The initial API check reached a successful password comparison but failed in
flash-buffer setup afterward. Authentication-only sessions now skip watchdog
and flash-buffer configuration, which is unnecessary for a password check.
Version 0.5.2 still failed flash setup with the Current password populated.

## Password attachment and buffering correction — 2026-10-20

The failure was reproduced on hardware after the reported power cycle.
With ordinary reset-and-halt, PFCR1/2/4 started at `0x00000601`,
`0x00000001`, `0x00000001`; clearing mask `0x51` succeeded on all three.
With the old password-first sequence, they started at `0x00432457`,
`0x00430057`, `0x00430057`. Prefetch bits cleared, but BFEN remained set
on each port. Core 0 was halted (`OSR=0x209`) and cores 1/2 were held in
reset (`0x041`) afterward. This identifies the connection sequence as the
trigger; it does not establish the hardware mechanism retaining BFEN.

Version 0.5.3 first halts under reset and checks lifecycle. Customer Delivery
uses a constant-time comparison of the readable installed slot, without
executing firmware for a password scan. A wrong password fails before setup.
Other lifecycle states or rejected ordinary debug access retain the C55
password scan path; USB failures do not trigger that fallback.

On the same board, the updated password connection produced the normal
reset-state registers and all three buffers cleared. PASS_LCSTAT remained
`0xA0000003`. No flash/OTP programming or protection changes were performed.
Two complete 32 KiB Config reads at `0x00FA8000`, one through ordinary
attachment and one through the live UI API with the installed password,
matched byte-for-byte at 1 MHz. The API reported a stored-password match,
and its status contained no credential. After the USB session closed,
reopening the adapter without MCU reset confirmed core states `0x209`,
`0x041`, `0x041`: primary halted, secondary cores held in reset.
The GW LED's meaning is board-specific; core debug status is the evidence
for the halt state, rather than LED activity alone.

## Faster flash operations — 2026-10-22

Version 0.7.0 defaults to 5 MHz and extends queued reads to CFLASH backups,
blank checks and verification. Complete nonblank 128-byte aligned MPC5748
CFLASH groups use one programming pulse, following the quad-page capability in
[NXP's MPC5748G data sheet, table 29](https://www.nxp.com/docs/en/data-sheet/MPC5748G.pdf).
Each queued buffer write uses the single-word protocol in
[AN4365 section 3.2](https://www.nxp.com/docs/en/application-note/AN4365.pdf).
All captured buffer-write statuses must succeed before EHV is asserted. The
buffer-loading queue never starts a program/erase pulse. A failed buffer is never retried or committed.
OTP, partial pages and all-ones ECC units retain the original programming path.

A hardware test loaded a 128-byte volatile program buffer in 0.003999 s at
5 MHz and then discarded it without an EHV pulse. Config was identical before
and after. Initially this established buffer transport only. The user subsequently
completed a full CFLASH erase and write on the same board using 0.7.0:

- Range: `0x00F90000`, 6,225,920 bytes; 5 MHz, 10 µs queued delay.
- Erase including backup and blank check: **496.47 s**; backup about 233 s.
- Write including backup and read-back verification: **2115.30 s**; backup about 238 s.
- The application reported successful verification and preservation of bytes
  outside the image. Firmware boot behavior was not evaluated.

These are user-provided operation logs, also present in the completed workspace
jobs. A separate later read failed at `0x01458200` with Nexus RWCS `0x10000006`
and MCR `0x00000600`; this must not be described as flawless full-read reliability.
Subsequent polling and three queued reads of a 1 KiB window at `0x01458000`
matched the loaded image. The intermittent fault was not reproduced or explained.

Offline tests exercise quad-page boundaries, partial pages, OTP exclusion,
all-ones units, pulse count, exact byte order, delayed writes and bus/USB errors.
Cancellation tests cover backup and the final pre-mutation checkpoint; after
modification starts, cancellation is rejected and the operation finishes.
PEmicro-equivalent erase/write speed has not been established. This remains a
host-driven implementation, with no target-resident programming loader.

## Write pipeline follow-up (0.7.1) — 2026-10-22

MPC5748 queued Write now reuses the blank check performed immediately after its
own erase, then verifies complete rebuilt blocks in one final pass. A changed
block is read three times instead of five. Backups must finish before any change;
unchanged blocks are skipped and still verified. No proof of blankness is reused
across connections. Changed blocks are still erased even if a backup reads all FF,
because that alone does not establish unused ECC. OTP/SHADOW paths are unchanged.

Each selected C55 controller read/write now uses one USB exchange. No subsequent
controller command is queued before checking the preceding reply. Delayed writes
are polled without replay. EHV, DONE, PEG and flash error checks remain explicit.
The final per-block verification covers preserved bytes outside partial images.

On the same board at 5 MHz, 64 cycles of MCR checks, PGM setup, 128-byte buffer load
and buffer discard took **1.651 s with the previous controller access** and
**0.336 s with queued controller access** (about 4.9x for this sequence).
Neither sequence asserted EHV. The full 32 KiB Config was unchanged afterward.
This does not establish an end-to-end erase/program time for 0.7.1; the completed
full write above used 0.7.0. Logs now report each Write phase separately.

After updating the running app, a full CFLASH Verify at 5 MHz compared all
6,225,920 bytes against the user's loaded image in **193.439 s**. Every byte
matched, with no fallback. No erase/program pulse was issued during the 0.7.1
hardware checks. The previous intermittent read error did not recur in this run.

## Queued CFLASH bench validation — 2026-10-22

Version 0.6.1 makes queued reads the default for MPC5748G CFLASH in the UI,
API and CLI at 100 kHz or faster. The standard polling override remains available.
The connected MPC5748G (`IDCODE 0x0988101D`) was checked read-only through
the live UI job queue, using Tigard channel B and the TRST-to-RESET mapping.

| Check | Result |
| --- | --- |
| Full CFLASH, 6,225,920 bytes at `0x00F90000`, 5 MHz, 10 µs delay | First read 236.556 s; repeated read 197.516 s; byte-for-byte identical; no polling fallback |
| Config, 32,768 bytes at `0x00FA8000` | Saved standard 1 MHz and queued 5 MHz reads match the Config slice of both full dumps |
| Fresh standard Config at 5 MHz | 25.685 s; matches saved Config; earlier queued Config took 1.208 s at the same clock |
| Independent standard samples at 5 MHz | 39 × 512 bytes: start, end, and both sides of all 37 CFLASH block boundaries; every byte matches the full queued dump |
| Updated app, API read mode omitted | Config defaults to queued: 2.993 s at 1 MHz and 1.033 s at 5 MHz; both match the saved standard dump; no fallback |

These are measurements from one board/host, not a guaranteed rate for every
target. The full array was read twice with queued mode; independent standard
comparison covers Config and the listed samples, not a full standard array read.
No flash, OTP, password or lifecycle programming was performed. Raw dumps and
the detailed bench report remain local and are excluded from this repository.

## Queued CFLASH reads — 2026-10-21

Version 0.6.0 introduced an experimental queued mode, disabled by default, for MPC5748G
CFLASH Read operations only. It retains the single-word Nexus protocol from
[NXP AN4365, sections 3.1/3.4](https://www.nxp.com/docs/en/application-note/AN4365.pdf).
The existing synchronous path remains in use for register setup, authentication,
OTP, erase/program/verification, and all other profiles.

Each queue contains at most 64 words. A hardware-clocked idle interval follows
each access request, then RWCS status and RWD data are captured without waiting
for the host between scans. The host checks every captured status before returning
the data. ERR fails with its word address. DV clear discards the entire batch,
ends the outstanding access, and switches the session back to polling. Partial
USB transfers fail. Queues cap replies at 2 KiB to leave FIFO headroom.
The idle-clock commands follow [FTDI AN_108, sections 6.5/6.6](https://ftdichip.com/Support/Documents/AppNotes/AN_108_Command_Processor_for_MPSSE_and_MCU_Host_Bus_Emulation_Modes.pdf).

The independent bit-clocked model verifies data, byte order, partial-word ranges,
multi-batch reads, delayed readiness, exact bus errors, short USB replies, and
fallback. A 256-byte read uses one USB write and one reply instead of 128 separate
status/data replies. Browser tests exercise the mode/delay settings and plan
propagation without starting a hardware operation.

With a 10 µs delay, the emitted scan sequence budgets about 426.5 seconds at
1 MHz or 97.7 seconds at 5 MHz for 6,225,920 CFLASH bytes. These are **clock-only
lower bounds**, excluding USB, host execution, setup, retries and storage; they
are not measured read times. Tigard was disconnected during the initial 0.6.0
change; the later 0.6.1 measurements above supersede that missing bench evidence.
The UI records actual duration, byte rate, and polling fallback.

## Offline verification

The unit suite covers TAP/MPSSE sequencing, OnCE/Nexus memory access, controller
geometry, timeouts, mismatched IDs, preservation across erase blocks, backup
failures, verification failures, persistent test doubles, and server API restrictions.
Additional tests cover SPC58H's small/large selector banks and 16-byte program
units, OTP preflight and repeat-write rejection, API OTP round trips, preservation
of SHADOW boot/security words, password lengths and slot locations, C90 serial
password order, secret cleanup, and monotonic multi-block progress. These tests
exercise independent controller models, not a physical MCU. Browser tests cover
both reset mappings, 14-pin and 10-pin connectors, SPI wiring, named saves,
read presets, OTP control availability and plan review, password-field lengths, hardware-mode presentation with a mocked adapter descriptor, and
continuous scrolling through a synthetic 16 MiB image.
Regression tests inject the factory UTEST ECC failure, verify exact read bounds,
retain unrelated bus errors, and check OTP backup addresses, contents, and
failure-before-programming behavior.

CI contains Python 3.10/3.13 jobs on Linux, macOS, and Windows, plus Linux browser
tests and distribution checks. A configured workflow is not a claim that these
hosted jobs have already run; check the repository's Actions results after push.
Local verification details are in [RELEASE_CHECKS.md](RELEASE_CHECKS.md).
Device models are confined to tests and excluded from the installed application.
The live CLI/API reject simulation requests, and missing hardware must fail
without generating a dump. No automated test intentionally programs physical hardware.

## Technical references

- [Tigard hardware, pin labels and FT2232H channel selection](https://github.com/tigard-tools/tigard).
- [NXP AN4224](https://www.nxp.com/docs/en/application-note/AN4224.pdf), JTAG/JCOMP design guidance.
- [ST automotive OpenOCD branch](https://github.com/STMicroelectronics/OpenOCD/tree/openocd-automotive-mcu-r1),
  `tcl/target/spc58xh.cfg`, `tcl/target/spc58.cfg`, and `src/target/powerpc.c`:
  SPC58 IDCODE `0x01146041`, six-bit IR, boot core selector, Nexus attributes,
  watchdog address, CFLASH/DFLASH windows and sector selectors. Referenced to cross-check parameters;
  no OpenOCD source or proprietary loader is bundled.

The MPC5748 configuration window and SPC58NH DFLASH window were also checked
against user-provided programming profiles. Those private profiles are not
distributed. Each supported window is bounded in code; it is not a general
read-any-address interface. See [SECURITY_OPERATIONS.md](SECURITY_OPERATIONS.md) for the password and OTP sources and remaining limitations.
