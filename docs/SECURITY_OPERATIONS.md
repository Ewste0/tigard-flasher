# OTP, SHADOW and passwords

This document describes version 0.6.1. Most OTP/password paths have only offline
tests. One MPC5748G has limited UTEST and installed-password checks; see
[VALIDATION.md](VALIDATION.md). Unlocking a protected MPC5748G is not yet bench
validated.

## Region operations

| Region | Read / verify / blank check | Program | Erase / patch write |
| --- | --- | --- | --- |
| SPC560 CFLASH, DFLASH | Yes | Erased 8-byte units | Yes |
| SPC560 SHADOW | Yes | Erased 8-byte units, with configuration validation | Preserves valid boot/security configuration |
| SPC560 CFLASH_OTP, DFLASH_OTP | Yes | Once per erased 8-byte ECC unit | Not supported by silicon |
| MPC5748 CFLASH | Yes | Erased 8-byte units | Yes |
| MPC5748 UTEST | Exclude factory ECC tests; subject to read protection | Once per erased 8-byte ECC unit | Not supported by silicon |
| SPC58NH92/EH92 CFLASH, DFLASH | Yes | Erased 16-byte units | Yes |
| SPC58NH92/EH92 UTEST | Subject to read protection | Once per erased 16-byte unit | Not supported by silicon |
| BAF | Subject to device permissions | Unavailable: factory boot area | Unavailable |
| HSM code/data | Subject to HSM/PASS permissions | Backend unavailable | Backend unavailable |

Read and verify operate on bytes. Blank check confirms that requested bytes read
FF; this is not proof of virgin ECC cells. Program skips all-FF units and rejects
any input range containing nonblank bytes before starting a pulse. Once a unit
has been programmed, do not reuse it, including with unchanged data. OTP backups
are for reference; they cannot reverse programming. A failed programming pulse
can leave an unusable ECC unit, so do not automatically retry.

Select an OTP region, load the binary, set the address, and choose **Program once**.
Review the address, image length, affected block, and irreversible-operation note.
The app saves the affected OTP units before programming and verifies afterward. Erase
and patch Write are disabled for OTP and rejected by the API/CLI before USB opens.
Reserved/factory fields and DCF formats remain device-specific: a raw OTP image
is not validated as a complete security or boot policy.

**Program once** is visible only for OTP regions in the UI. Use **Write** for
ordinary CFLASH/DFLASH. The CLI's low-level `program` operation remains available
for already-erased ordinary flash.

### MPC5748G UTEST reads

Selecting UTEST defaults to **UTEST config + user**, the contiguous range
`0x004000A0..0x00403FFF` (16,224 bytes). The first 160 bytes are excluded; they
include factory ECC test patterns. This is not a complete 16 KiB raw UTEST image:
file offset zero corresponds to physical address `0x004000A0`.
The separate **UTEST user OTP** preset reads `0x00401000..0x00403FFF` (12 KiB).
Password fields in the configuration range can still be unreadable after a
lifecycle transition; these reads fail rather than inventing bytes.

On the tested MPC5748G, reading `0x00400060` produced `RWCS=0x10000006` and
`MCR=0x00008600` (ECC error). This is consistent with the factory double-bit
ECC test patterns described in NXP AN13179. Selecting the full 16 KiB block
still encounters those patterns. The driver reports the failing physical
address and controller status and does not return a fabricated full dump.

OTP backups cover the aligned units to be changed, not the entire UTEST block.
Installing a C55 password therefore backs up its 32-byte slot, avoiding unrelated
factory tests and protected slots. Backup failure or nonblank target data stops
the operation before programming. Backups for erasable flash still cover full
affected erase blocks.

SHADOW erase clears its user area, then restores the configuration tail starting
at offset `0x3DD0`. It does not leave the entire sector blank. Patch write validates
both censorship words before erasing and restores configuration before user data.
Power loss during that erase/rebuild can still lose configuration. A backup cannot
prevent that interruption. Reserved fields are preserved when patching a smaller
image; a complete supplied SHADOW image must preserve the intended device settings.

## Password lengths and implemented capabilities

| Family | Bits | Bytes | Hex digits | JTAG login | Install password |
| --- | ---: | ---: | ---: | --- | --- |
| SPC560B54/B60/B64 | 64 | 8 | 16 | Implemented; bench unvalidated | SHADOW erase/preserve/rebuild |
| MPC5748G | 256 | 32 | 64 | Entry implemented; stored-password check on Customer Delivery; protected unlock unvalidated | JTAG or PASS group 0–3, OTP |
| SPC58NH92/EH92 | 256 | 32 | 64 | Unavailable pending verified serial protocol | JTAG or PASS group 0–3, OTP |

**32 bytes is not 32 bits.** Enter hexadecimal without `0x`. The CLI accepts
whitespace between words. Input follows increasing flash address: NVPWD0 then
NVPWD1 on SPC560, or the eight UTEST words in address order on C55. SPC560 login
translates those words into the held-reset 65-bit CENSOR_CTRL scan (64 password
bits plus its enable bit). Each SPC560 16-bit password halfword must contain both
a zero and a one when installing a private password.

SPC560 passwords occupy `0x00203DD8..0x00203DDF`. On both listed C55 families,
JTAG occupies `0x00400120..0x0040013F`; PASS groups 0–3 start at `0x00400140`,
`0x00400160`, `0x00400180`, and `0x004001A0`, each 32 bytes.

Installing a password preserves censorship/lifecycle/DCF settings. It does not
automatically enable protection. JTAG authentication and PASS-group unlocking
are different protocols; a JTAG password does not unlock all flash protections.
PASS-group authentication and automatic protection activation are not implemented.
Opening an already uncensored SPC560 cannot establish that a supplied password
was correct. Protected targets may deny backups/readback and cause installation
to fail before programming.

### MPC5748G Open with password

Enter all 64 hex digits in **Current password**, then use **Open with password**.
The same input is supplied on subsequent operations, which each open a fresh
reset/halt session. Leave it empty for an ordinary connection.
Power-cycle the target after programming a password before checking login.
Do not program the occupied OTP slot again.

The MPC5748G connection first attempts an ordinary halt under reset and reads
PASS_LCSTAT. In Customer Delivery (LIFE=3), it compares the supplied password
against the installed slot while the cores remain halted or held in reset.
It does not run firmware or submit a JTAG password scan on this path. Even a
pre-existing JUN flag cannot substitute for matching the supplied password.

If ordinary debug attachment is rejected, status cannot be read, or the lifecycle
is not Customer Delivery, the C55 password path releases external reset before submitting the eight 32-bit words,
in flash-address order and LSB-first within each word, through instruction 7 and
a 256-bit DR. There is no C90 enable bit and no TAP reset after submission.
Firmware may execute before the halt request; this is not a reset-vector halt.
At most one password is submitted per connection, without alternative encodings/retries.

After halting, the app reads PASS_LCSTAT. A set JUN flag produces a
`jtag_status` result with `unlock_verified: true`. When JUN is clear and LIFE
is Customer Delivery (3), debug access is already allowed. The app compares the
input against the readable 32-byte JTAG slot and reports `stored_password`,
`password_verified: true`, and `unlock_verified: false`. A mismatch fails the
operation before watchdog or flash-controller setup. In other lifecycle states
with JUN clear, login fails without reading the protected password slot.
An unreadable status or slot also fails; accessible debug alone never establishes
a successful MPC5748G password check.

An authentication-only session does not configure flash buffering or watchdogs.
Read/write sessions still require their normal setup. Version 0.5.3 fixes the
observed Customer Delivery buffering failure by avoiding the unnecessary
firmware-execution interval. The Current password field can remain populated
for ordinary reads/writes. If buffer controls still fail to clear, the error
includes the register address and before/after values, and preparation stops
before erase/program. This condition is not silently ignored.

The current bench target remains in Customer Delivery. Its JUN flag did not
assert during the scan, including after a power cycle. The stored-password
comparison is therefore the validated path on this board. **This is not evidence
that a protected target can be unlocked.** Do not change lifecycle/DCF merely
to test the driver. SPC58H login remains disabled, with its current-password
field disabled so an unused credential cannot block normal operations.

Passwords are omitted from plans, status JSON and logs. Pending plan secrets
remain only in server memory until the job ends or the plan is evicted. Current
login input remains in the browser until cleared/reloaded or the chip changes.
Backups and raw dumps can contain existing passwords and security data; keep
them private. They are not removed from the device image.

CLI examples (password files contain only hexadecimal text):

```sh
python -m spc5tool blank spc560b64 --address 0x400000 --length 0x2000
python -m spc5tool program spc560b64 otp.bin --address 0x400000
python -m spc5tool authenticate spc560b64 --password-file current-password.txt
python -m spc5tool install-password spc58nh92 --new-password-file new-password.txt --slot jtag
```

The `program` and `install-password` commands initially print a plan. To execute programming or
installation, add `--execute --backup NEW_DIRECTORY --expect-idcode ID` and the
connection/reset options appropriate to the board. Password file contents are
never printed or hashed into a public plan.

## Sources and unresolved details

- NXP MPC5607B Reference Manual rev 7.1, flash memory/configuration and JTAG
  sections; ST [RM0037](https://www.st.com/resource/en/reference_manual/cd00234622.pdf)
  for the SPC560B family: 16 KiB SHADOW, 8 KiB user portions of the two test/OTP
  arrays, ECC width, censorship configuration, and low/mid/high block selectors.
- NXP support's [CENSOR_CTRL sequence](https://community.nxp.com/t5/MPC5xxx/SPC5646-uncensoring-device-via-JTAG/m-p/1392647/highlight/true):
  hold reset, supply password with enable bit, then release reset.
- NXP [AN12092](https://www.nxp.com/docs/en/application-note/AN12092.pdf), figures
  1 and 3: MPC5748 UTEST/password map, 256-bit lengths, OTP restriction, and
  ordinary/HSM flash block mapping. PASS register input is distinct from JTAG scan input.
- [Lauterbach Qorivva debugger manual](https://www2.lauterbach.com/pdf/debugger_mpc5500.pdf),
  release 02.2026, Censorship Unlock (pp. 47–49), KEYCODE (p. 101), and ResBreak
  (p. 108): MPC574xB/C/D/G reset-release restriction and eight-word input order.
- NXP [MPC5777C reference manual](https://community.nxp.com/pwmxy87654/attachments/pwmxy87654/mpc5xxx/19572/1/MPC5777CRM%2C%20MPC5777C%20Reference%20Manual.pdf),
  sections 55.4.4, 55.5.2, 55.5.4 and 67.3.1: related MPC57xx 256-bit register,
  opcode 7, shift direction, and PASS status semantics. This cross-check is not
  a substitute for a protected MPC5748G bench test; that part uses a six-bit IR.
- NXP [AN13179](https://www.nxp.com/docs/en/application-note/AN13179.pdf), section 9:
  MPC57xx factory ECC/EDC test patterns, including the double-bit error test
  at `0x00400060`. This address was reproduced on the MPC5748G bench.
- ST [RM0452](https://www.st.com.cn/resource/en/reference_manual/rm0452-spc58-h-line--32-bit-power-architecture-automotive-mcu-triple-z4-cores-200-mhz-10-mbytes-flash-hsm-asild-stmicroelectronics.pdf),
  table 14: SPC58H password offsets. The full download could not be retrieved in
  this environment; the indexed table confirms slot addresses, not JTAG wire order.
- ST [DS12304](https://www.st.com/resource/en/datasheet/spc58nh92e7.pdf), flash
  tables and core availability; ST's [automotive OpenOCD source](https://github.com/STMicroelectronics/OpenOCD/tree/openocd-automotive-mcu-r1),
  `spc58xh.c` and target configuration: H-line sector mapping, boot core and
  Nexus attributes. Parameters were cross-checked; no upstream algorithm blobs
  or proprietary PCP files are included.
- PEmicro [SPC58 password discussion](https://www.pemicro.com/forums/forum_topic.cfm?forum_id=10&forum_topic_id=8700)
  confirms 256-bit passwords and separate security settings. Its UI is not evidence
  that OTP supports erase.

Protected MPC5748G authentication, SPC58H serial login, HSM quiescence/ownership,
and complete security-policy activation need further device-specific validation.
The app does not represent them as fully qualified support.
