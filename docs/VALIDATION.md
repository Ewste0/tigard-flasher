# Validation and scope

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

The full main-array read was canceled. Full-array writing, other configuration
blocks, other MPC5748 boards, all SPC560/SPC58 variants, and SPI remain unvalidated on
hardware. The CLI's `hardware_validated: false` means the entire profile is not
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

## Queued CFLASH reads — 2026-10-21

Version 0.6.0 adds an experimental queued mode, disabled by default, for MPC5748G
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
are not measured read times. A three-minute full read has not been verified.
Tigard was disconnected during this change, so no live queued reads or writes
were performed. Compare a Config dump against a standard read before trying a
larger range. The UI records actual duration, byte rate, and polling fallback.

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
