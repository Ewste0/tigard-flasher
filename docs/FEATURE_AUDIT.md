# MCU, OTP and PROGPPCNEXUS audit

Reviewed 2026-10-25 for version 0.7.6. Scope: all six shipped MCU profiles,
their mapped regions, controller operations, security paths, transport failures,
file/backup handling, and UI/API/CLI operation guards. The W25Q128JV ordinary
SPI array path was also reviewed. This is a source/static-evidence and offline
test audit; it is **not hardware certification of OTP on every MCU**.

The user reported the 0.7.5 SPC560 connection fix working. Earlier MPC5748G
read/write results are recorded in [VALIDATION.md](VALIDATION.md). No adapter
was opened, vendor executable run, or physical OTP/security byte programmed
for this audit. Manufacturer manuals establish register requirements; static
P&E analysis establishes code paths, not measured vendor execution or timing.

## Findings and corrections

| Finding | Effect before correction | 0.7.6 correction / regression evidence |
| --- | --- | --- |
| A1: SPC560 SHADOW selector | TSLK bit 20 was written into reserved LMS, preventing erase and SHADOW-based password replacement | SHADOW selected by interlock address; bit 20 remains only in locks. Controller model masks reserved bits. Tests cover all three SPC560 variants and neighboring arrays |
| A2: SPC560 censorship validation | Valid uncensored public-serial configurations were rejected | Require matching NVSCC and a legal SC/CW combination; test valid configurations and permanent-lockout rejection |
| A3: Generic UTEST programming | Any blank aligned UTEST location could receive arbitrary bytes, including lifecycle/DCF/factory fields | API, CLI and driver enforce documented customer windows. A separate exact-slot password path retains blankness, backup and verification checks |
| A4: USB failure during lock restoration | Cleanup continued after TAP/write completion became unknown | Poison the session and stop remaining I/O immediately; inject failures into C90 keys/values and C55 locks |
| A5: Reviewed image changed on disk | Execution could use different bytes from the reviewed upload | Validate complete decoded image length and SHA-256 before opening hardware; cover binary and sparse S-record uploads |
| A6: SPC58 UTEST defaults/revision | Full-block default crossed factory diagnostics; cut restriction was not prominent | Customer OTP read preset, family-specific ECC guidance, explicit cut 2.0 label; physical read map retained |
| A7: Password completion guidance | SPC560 omitted the required power-cycle instruction | All families receive it; SPC58 still states login unavailable |

## Per-profile OTP review

Addresses below are inclusive. Program requires complete aligned units, an
entire-range blank preflight and post-write byte verification. Fully FF input
units receive no programming pulse. Erase and patch Write are rejected for OTP.

| Profile | Generic customer OTP windows | Program unit | Qualification |
| --- | --- | ---: | --- |
| SPC560B54 | CFLASH_OTP `00400000–00401FFF`; DFLASH_OTP `00C00000–00C01FFF` | 8 bytes | Documentation + offline boundary/controller tests |
| SPC560B60 | Same two windows | 8 bytes | Documentation + offline boundary/controller tests |
| SPC560B64L3 | Same two windows | 8 bytes | Documentation + offline boundary/controller tests; connection success reported separately |
| MPC5748G / SPC5748G profile | UTEST `00401000–00403FFF` | 8 bytes | Documentation + offline tests; earlier limited UTEST/password bench record |
| SPC58NH92 cut 2.0 | UTEST `00401000–00401FFF` | 16 bytes | Documentation + offline tests; no live qualification |
| SPC58EH92 cut 2.0 | Same UTEST window | 16 bytes | Documentation + offline tests; no live qualification |

Evidence: [ST RM0037](https://www.st.com/resource/en/reference_manual/rm0037-support-microcontrollers-spc560b54x-spc560b60x-and-spc560b64x-stmicroelectronics.pdf)
and [MPC5607B RM, chapter 30](https://community.nxp.com/pwmxy87654/attachments/pwmxy87654/mpc5xxx/15469/1/MPC5607B_Reference_Manual.pdf)
for C90; [NXP AN12092](https://www.nxp.com/docs/en/application-note/AN12092.pdf)
for MPC5748; [ST RM0452, table 14 and section 9.3](https://www.st.com.cn/resource/en/reference_manual/rm0452-spc58-h-line--32-bit-power-architecture-automotive-mcu-triple-z4-cores-200-mhz-10-mbytes-flash-hsm-asild-stmicroelectronics.pdf)
for H-line. ST's full PDF download failed; official indexed pages were used.
[ES0429 page 1](https://www.st.com/resource/en/errata_sheet/es0429-spc58ehx-spc58nhx-devices-errata-jtagid--0x01146041-stmicroelectronics.pdf)
ties the accepted H-line IDCODE to cut 2.0. An 8-byte blocking mask in a supplied
PCP header does not override H-line's 128-bit ECC requirement.

## Other operations and P&E comparison

P&E capabilities below follow its [official command reference](https://www.pemicro.com/manuals/progppcnexus/programming-commands/).
They depend on the selected device/algorithm. This matrix is a capability
comparison, not a claim to reproduce every vendor algorithm.

| Operation | PROGPPCNEXUS reference | Tigard implementation / difference |
| --- | --- | --- |
| Read / display | UM, UR, SM | Mapped reads, named BIN downloads, scrolling hex/ASCII viewer; no S-record export |
| Program / verify | PM, PR, VM, VR | BIN and strict S-record input; explicit sparse-segment selection; byte verification |
| Erase / blank | BE, EM, BM | Whole-block erase and blank check; SHADOW preserves its configuration tail; no OTP erase |
| Image queue | QO and queued inputs | One selected image/segment; no merged queue, Intel HEX or ELF input |
| CRC / serialization | SC, VC, VV, QS | Not implemented; file SHA-256 is not target CRC verification |
| Algorithms | Selected PCP module | Six reviewed MCU profiles; independent host-driven controllers, no proprietary RAM loader |
| Security | Device-specific algorithms/settings | Password slots supported; protection activation, DCF authoring, PASS-group unlock and SPC58 JTAG login unavailable |
| HSM / BAF | Algorithm/device dependent | Mapped reads subject to access permissions; no write/erase backend |
| Relays / calibration | Adapter/algorithm dependent | Not implemented; Tigard power automation is not implied |

Static comparison of the supplied P&E executable and decoded C90 loader agrees
with the PGM/load/EHV/DONE/PEG sequencing; the manual establishes SHADOW selection.
The vendor loader can retain PGM across units; ours restarts it per unit. The
earlier connection comparison and executable SHA-256 are in
[VALIDATION.md](VALIDATION.md). No proprietary code, PCP or firmware is shipped.
Equivalent erase/write speed has not been established.

## Verification and remaining boundaries

The added audit tests cover every C55 erase sector and selector, C90 low/mid/high
and DFLASH sectors, OTP edges/alignment/reuse rejection, SHADOW isolation,
all five C55 password slots, backup boundaries, USB failure cleanup and modified
reviewed images. Existing tests cover TAP/OnCE/Nexus sequencing, queued status
checks, timeouts, SPI page/sector preservation, file parsing, cancellation and
secret handling. Browser tests check presets, operation controls and scrolling.
Results are recorded in [RELEASE_CHECKS.md](RELEASE_CHECKS.md).

| Audit question | Status / remaining evidence needed |
| --- | --- |
| Implemented geometry, supported operation guards and software sequences | Reviewed; confirmed defects fixed and offline regressions added |
| Password lengths, slots and preservation | Reviewed; 16 hex digits for SPC560, 64 for C55; provisioning does not activate protection |
| OTP works physically on all six profiles | Unresolved; representative device/revision tests with known-unused units, read-back and power-cycle verification are still required |
| Protected-target authentication | Unqualified on hardware; accessible debug or a readable stored-password match does not establish protected unlock |
| Revision coverage | H-line cut 2.0 only; remaining profiles are not a maskset-wide certification |
| Complete PROGPPCNEXUS parity | Not achieved; explicit gaps listed above |

Reading FF cannot prove that an external programmer never pulsed that ECC unit.
An interrupted OTP/password write can permanently consume a unit; there is no
rollback or automatic retry. SHADOW erase/rebuild is not atomic during power loss.

Factory diagnostic areas can intentionally fail reads. C90 corrected-ECC EDC
status is not currently surfaced, so a successful read does not certify ECC
health. MPC5748 HSMData can suppress ECC reporting and substitute `0x15551555`;
a successful transfer is not proof of intact HSM contents. See
[NXP AN13179, table 1 and sections 9.2–9.4](https://www.nxp.com/docs/en/application-note/AN13179.pdf).
No continuous raw UTEST dump is promised across diagnostics or protected slots.
W25Q128JV support covers the ordinary array only; SPI security/OTP registers
remain outside scope. Details and user workflow: [SECURITY_OPERATIONS.md](SECURITY_OPERATIONS.md).
