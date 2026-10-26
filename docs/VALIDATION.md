# Validation and scope

## SPC560B64L3 Speed Write/Erase — 0.8.0, 2026-10-26

The user requested speed without backups. Speed is limited to complete ordinary
CFLASH/DFLASH blocks on SPC560B64L3. It removes backup, array blank checking and
automatic final read-back verification. Controller success is not byte-for-byte
verification; the result explicitly reports `verified: false`. Standard mode
retains its previous checks and partial-image preservation. No OTP, SHADOW,
password or other-profile operation uses Speed.

[ST AN4194 section 2.1, steps 7–9](https://www.st.com/resource/en/application_note/an4194-flash-programming-for-spc560xxrpc560xx-spc56apxx-rpc56apxx-and-spc563m64xx-devices-stmicroelectronics.pdf)
allows retaining PGM between double-word programming operations. Clear EHV
after DONE/PEG, load the next interlock/data, then assert EHV again. Clear PGM
at the end. The implementation follows that sequence and retains lock/selector
read-back checks and instruction/controller errors. It always erases before
programming; an all-FF input still erases, but sends no program pulses.

Independent clocked OnCE and controller fixtures count 19 USB submissions and
10 response reads per additional nonblank 8-byte unit, compared with 37/20 in
Standard 0.7.9. Shared setup/cleanup and extra busy polls are excluded. Tests
check CFLASH/DFLASH bytes across gaps and block boundaries, untouched neighbors,
one grouped erase, restored locks, no array reads, retained PGM, FF skipping,
timeouts, failed PEG/ECC status, disconnects, and safe Stop. USB loss prevents
all further transport activity. API/CLI tests reject invalid ranges before
opening a backend and distinguish unchecked completion, cancellation and errors.

The UI defaults to Speed only for these main arrays. The API defaults to false
and CLI requires `--speed`; partial BIN/S-record images must use Standard.
Offline tests do not measure silicon erase/program time or prove live behavior.
**A real SPC560B64L3 benchmark and read-back comparison remain outstanding.**

## SPC560 write result captures — 0.7.9, 2026-10-26

The user identified the slow remote target as SPC560B64L3 at 5 MHz. The queued
10 µs setting applies only to MPC5748 CFLASH; it never enters the SPC560 OnCE
path. The exact remote software version and phase timing are still unavailable.

Previously, both instructions in every OnceMemory.write32() retrieved CPUSCR
after checked execution, although the caller discarded the returned WBBR.
The operand setup and store now omit only those unused result scans. OSR must
still report successful instruction completion before any next instruction.
Reads and both initialization self-tests still retrieve/check their results;
error diagnostics and transport failure propagation are unchanged.

A coupled independent bit-clocked OnCE model and C90 controller model compares
one and two fully programmed ECC units. The marginal cost of each additional
8-byte unit drops from 51 USB writes / 34 response reads to 37 / 20. This excludes
shared unlock/lock restoration and separate backup/verification passes, and
assumes no extra busy-status polls. The Flash register-write log is identical,
the stored bytes verify, and controller/lock state is restored. Additional
tests cover delayed completion and USB failure at either instruction's status
read, preventing subsequent instructions or replay.

The 8-byte sequence remains consistent with
[ST UM1627](https://www.st.com/resource/en/user_manual/um1627-standard-software-driver-for-c90lc-flash-module-embedded-on-spc56-m-b-c-d-and-p-lines-microcontroller-stmicroelectronics.pdf).
No flash pulse, DONE/PEG/error check, blank preflight or final read-back verify
was removed. A target-resident programming routine remains unimplemented.
**No real-device elapsed-time improvement has been measured.**

## Stop and SPC560 USB submissions — 0.7.8, 2026-10-26

The report of a 1.5 MB write at 13% after four minutes came from a colleague's
remote device. No local hardware was connected, and the exact phase/MCU has not
been established. It is not a controlled write benchmark. In the current
ordinary Write pipeline, 0–20% is backup; the overall percentage cannot be
extrapolated linearly to remaining time.

SPC560 register accesses previously sent each TAP transition and IR/DR scan as
separate USB writes. The new packaging compiles one register access into one
MPSSE submission. Independent bit-clocked tests compare the complete TMS/TDI
stream before/after and verify identical data. One word read takes 3 USB writes
instead of 15, with the same two response reads and the same checked
GO → OSR → CPUSCR order. Pending DEBUG is polled; errors stop before the next
instruction. This is a traffic reduction, **not a measured 5× speed increase**.
It does not introduce a RAM programming loader or a speculative instruction queue.

The configured FTDI latency timer is now 1 ms instead of the PyFtdi MPSSE
default of 16 ms. Existing SEND_IMMEDIATE commands already flush replies; this
change alone does not imply a 16× gain. References:
[PyFtdi MPSSE API](https://eblot.github.io/pyftdi/api/ftdi.html#pyftdi.ftdi.Ftdi.open_mpsse_from_url)
and [FTDI AN_108](https://ftdichip.com/Support/Documents/AppNotes/AN_108_Command_Processor_for_MPSSE_and_MCU_Host_Bus_Emulation_Modes.pdf).

Stop requests are accepted during MCU modification. Driver checkpoints occur
after controller pulse cleanup, between programming units/quad-pages and read
chunks. Volatile locks restore before the cancelled result is returned. SHADOW
rebuilds defer Stop through configuration restoration and verification; real
transport/controller errors still propagate immediately. Password installation
and SPI modification are outside this cancellation change.

Tests request Stop during erase/program EHV on every MCU profile, check the
controller is idle, verify lock restoration, ensure the next unit is untouched,
retain original backups and distinguish incomplete results from success.
HTTP tests cover erase, Write, OTP and SHADOW, including downloadable backups.
Browser tests exercise pending Stop and cancelled results at widths 1440, 1024,
850 and 480 px. They start no hardware operation.

Physical validation remains outstanding: connect the actual part, compare a
small read against the prior version, then record complete phase timings on an
authorized write. Neither P&E-equivalent speed nor all-MCU OTP qualification
is established by these offline checks.

## Grouped erase and ordinary Write — 0.7.7, 2026-10-25

Ordinary flash erase previously restarted the controller for every block.
The implementation now combines only the selected geometry masks and performs
one erase per controller/region. Full CFLASH uses one operation for each profile:
10 blocks on B54, 12 on B60, 16 on B64, 38 on MPC5748G, or 48 on SPC58H.
DFLASH's four blocks also share one operation. Different regions/controllers
execute sequentially. SHADOW uses its separate interlock path and OTP stays
non-erasable. Partial selections never enable neighboring blocks or reserved bits.

[ST UM1627 section 2.6.2](https://www.st.com/resource/en/user_manual/um1627-standard-software-driver-for-c90lc-flash-module-embedded-on-spc56-m-b-c-d-and-p-lines-microcontroller-stmicroelectronics.pdf)
and [UM2636 section 2.7.2](https://www.st.com/resource/en/user_manual/um2636-standard-software-driver-for-c55-flash-module-embedded-on-spc58-b-c-e-h-g-and-n-lines-microcontroller-stmicroelectronics.pdf)
permit combined main-array selections. Both also require checking the result:
locked blocks may report successful completion without erasing. Every selected
byte is therefore still blank-checked. The active timeout retains the previous
total per-block allowance; the cleanup timeout remains unchanged. This is a
maximum wait budget, not an added pause or an assumption of parallel silicon erase.

Static P&E C90 loader evidence at `0x40000260..0x400002F6` shows combined low/mid/high
selectors followed by one interlock/EHV sequence. Its blank checker at
`0x40000134..0x4000014C` scans words on the target CPU. Our blank checker still
reads bytes through JTAG. This difference and the mandatory backup can dominate
elapsed time; grouping is not a claim of P&E-equivalent end-to-end speed.
No vendor code was copied and no hardware was erased for this change.

Ordinary Write now uses the same three-pass pipeline for all MCU profiles:
durable complete-block backups, grouped erase with blank check, programming,
then complete-block verification. Unchanged blocks are excluded from erase and
program but are backed up and verified. For a fully changed image this reduces
SPC560/SPC58 reads from five to three full passes; MPC5748's optimized path
already used three. All changed blocks are erased before programming starts;
an interruption can leave several blocks blank, so the backup remains essential.
SHADOW restoration order and OTP programming are unchanged.

Offline tests exercise all six profiles, full and sparse masks, untouched gaps,
lock restoration, selector failures, false-positive erase status, USB failures,
scaled active deadlines, cancellation and progress. Traffic tests count exactly
three full reads of changed blocks and two of unchanged blocks, while checking
the entire resulting array against expected preserved contents. UI/API/CLI
report controller erase separately from backup and blank-check time. A real
target benchmark is still needed; no new timing estimate is presented as a measurement.

## Full profile and OTP audit — 2026-10-25

Version 0.7.6 audits every shipped MCU profile and compares supported operations
with PROGPPCNEXUS. See [FEATURE_AUDIT.md](FEATURE_AUDIT.md) for the findings,
per-profile OTP ranges, primary sources, comparison matrix and open questions.
SHADOW selection/censorship, UTEST write policy, USB cleanup, reviewed-image
integrity and family-specific diagnostics were corrected. No hardware was
connected or programmed during this audit.

After 0.7.5 the user reported that the SPC560 fix worked. This is user-reported
connection success, not an independently captured full dump or proof of OTP,
SHADOW programming or protected-password authentication on that device.

New tests cover all six profiles, all C55 erase selectors, OTP boundaries and
ECC units, preserved locks/neighboring data, exact password slots and rejection
before USB access. Controller models do not emulate silicon ECC history,
electrical timing or lifecycle behavior. Physical OTP qualification remains
outstanding, as detailed in the audit.

## SPC560 connection comparison and failure capture — 2026-10-25

The user retested v0.7.4: the register-only instruction `0x1BE1D001` also failed
with OSR `0x309` and next PC `0xFFFFC004`. P&E Micro reportedly reads the same
chip successfully. Thus moving the fetch to ROM did not resolve the observed
failure, and the OSR alone cannot establish a hardware, authentication or
instruction-fetch cause.

Static comparison of the supplied `progppcnexus.exe` (SHA-256
`ac12ff11801465d7277876b19bdf40f3e288e9c78628bc0fd15f4d727090102e`)
recovered these protocol facts, without running it or connecting an adapter:

- The single-core initialization helper at VA `0x0057D544`, called before
  reset release by the connection routine at `0x00580224`, writes OCR with
  DR/WKUP/FDB and writes DBCR0 twice to establish EDM and initialize its fields.
- The instruction helper at VA `0x005774AC` independently adds CTL bit `0x2`
  for VLE and `0x400` for operand override. It submits CPUSCR with GO set and
  EX clear (`0x110`), then scans status before retrieving the result.
- The status checker at VA `0x00575E80` requires `(OSR & 0x30F) == 0x209` for
  a completed step. P&E does not treat `0x309` as successful execution.
- The MPC560x/SPC560xxx profile uses a BAM instruction PC of `0xFFFFC000`.
  Its MSR template is `0x02003000`, while our injected MSR remains zero.
  This release does not copy the broad template's unsupported SPE/FP bits or
  change machine-check exception handling without target evidence.

The SPC560 connection now arms DR/WKUP/FDB and initializes DBCR0 twice under
RESET. It retains post-halt reinitialization, then clears DBSR and changes OCR
to WKUP/FDB before executing instructions. Unused z0h OCR memory-attribute bits
from the generic P&E helper are omitted. SPC560 instruction submission now
uses one full CPUSCR+GO scan; the scan's UPDATE-DR starts exactly one step.
There is no second GO or automatic replay. Other families retain their previous
reset and GO sequence. This is an independent implementation of the observed
protocol, not a claim of complete P&E behavior or adapter timing equivalence.

The outgoing CTL value was still `0x400`: code coupled IRStat8/VLE to `has_mmu`.
VLE instruction format and address translation are separate properties. Although
the generic NXP AN4365 describes IRStat8 as reserved for z0,
[ST AN4035 sections 2.7.2 and 2.8](https://www.st.com/resource/en/application_note/dm00046345-flash-programming-through-nexus-jtag-stmicroelectronics.pdf)
specify setting the instruction-format flag appropriately for SPC56 VLE
injection. [ST RM0046, IRStat8 description](https://www.st.com/resource/en/reference_manual/rm0046-spc560p34spc560p40-32bit-mcu-family-built-on-the-embedded-power-architecture-stmicroelectronics.pdf)
also describes the tag as always set on z0h and used to determine IR format.
Version 0.7.5 explicitly sends VLE=1 for these VLE opcodes, yielding CTL `0x402`
with operand override and `0x002` without it. MMU-equipped injection retains
the same encoding as before; Nexus memory transactions do not use this path.

To make another failure actionable, a valid ERR status with DEBUG and MCLK set
and RESET clear triggers read-only CPUSCR/OCR/DBCR0/DBSR capture. The error
includes the submitted CTL and captured PC, IR, MSR and CTL. It excludes both
WBBR halves and the supplied operand, which can contain user data or credentials.
No instruction is replayed, no exception is cleared, and no memory operation is
performed by capture. Transport failures, timeouts, invalid scans and inactive
cores do not trigger diagnostic scans; a capture failure stops further I/O.

Regression tests check the instruction-format tag for every SPC560 profile,
reject an untagged VLE packet in the protocol model, and exercise diagnostic
failures, no-replay behavior and credential exclusion. The model encodes the
documented format requirement; it does not prove that the missing tag caused
this particular target's ERR. No physical SPC560 was accessed during development.

SEGGER comparison: the installed J-Link V8.64 device database includes SPC560B64.
However, the support icons on the
[official SPC560B64 product page](https://www.segger.com/supported-devices/st/spc56/spc560b64/)
mark J-Link and Flasher ARM unsupported, while Flasher ATE2, Compact, Portable
PLUS, Pro and Pro XL are supported. A database entry alone does not establish
ordinary J-Link support. [SEGGER's SPC56 notes](https://kb.segger.com/ST_SPC56)
also distinguish shadow-sector programming and watchdog handling. No SEGGER
hardware transaction trace was captured; this comparison is not a verified
connection recipe for the user's SPC560B64L3.

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
