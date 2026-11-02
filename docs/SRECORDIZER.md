# SRecordizer dump editor

Available for SPC560B64L3 and SPC5668G/MPC5668G. Parsing and editing run locally
without USB. The formats were compared with the supplied SRecordizerMac Swift
implementation; no vendor executable or customer dump is bundled.

## Workflow

1. Select the actual MCU and open a dump. For BIN, set the physical address of
   file offset zero before scanning. S-record files carry their own addresses.
2. Open SRecordizer and select Tesla parameters, VCSEC journal, SPC56 text or
   HEX/ASCII. Select a record, edit its value and apply the change.
3. The new revision becomes the loaded image. Undo/Redo changes the selected
   revision without overwriting the original upload. Save with Export S19 or BIN.
4. Review Write uses that revision's hash and the selected physical segment.
   Existing reviews are invalidated after an edit. Verify remains available.

Multi-segment S-records retain all segments and gaps. BIN export contains only
the selected segment. Write also acts on one selected segment; review and write
other edited segments separately. Edited files cannot silently move to a different
MCU or binary base address. Reopening an exported BIN requires setting its base.

SPC560B64 Speed only accepts complete CFLASH/DFLASH blocks and omits backups and
automatic verification. Select Standard for partial-block patches. The editor
does not change flash operation policy or start a hardware job on Apply.

## Supported structures

| Section | Inspection and edits |
| --- | --- |
| Tesla | CRC8 candidates, parameter ID/name, fixed-length values, existing VIN |
| VCSEC | Page/journal state, current/history/deleted records, schema and CRC32; supported current records only |
| VIN 0033 | Format 1, schema 1, 21-byte payload: first 17 bytes VIN, last four preserved |
| Public keys | Twenty slots, P-256 point and duplicate checks; clearing a slot also clears its associated current per-slot secret |
| Certificate | Complete 1002-byte blob import/export and recognized VIN field; no signing or signature-validity claim |
| SPC56 text | firmware.rc and internal.dat at their recognized physical zones; bounded growth and preserved FF padding |
| HEX / ASCII | Addressed byte edits, search and equal-length replace, bounded to existing segments |

Structured edits repair their supported CRC8/CRC32 and file-level S-record
checksums. Raw edits repair only S-record checksums, not internal firmware
checksums. Changed S-record lines preserve record type/address/length; unchanged
lines, ordering, headers, entry records and line endings stay intact.

Corrupt, deleted, historical, ambiguous and unsupported VCSEC records are
read-only in structured mode. CRC8 candidates alone do not establish a firmware
schema; recognized VCSEC/SPC56 images suppress speculative Tesla scanning unless
explicitly requested. VIN editing updates the selected field, not every VIN in
the file. No automatic journal append, counter changes or authentication bypass
is implemented. Target firmware acceptance still requires device testing.

## Optional parameter names

Import names accepts a local JSON array with `id`, `name` and optional `type`,
`description`, `values`. It is validated and stored as `srecordizer-catalog.json`
inside the ignored workspace directory. Generic names and known VCSEC roles work
without it. The extracted dictionary supplied with another application is not
redistributed with this package.

## Validation

Synthetic parser, HTTP and browser tests cover CRC updates, immutable revisions,
Undo/Redo, correct Write selection, sparse exports and responsive layouts. A
private corpus of nine S-record images matched the supplied Swift parser's 125
VCSEC entries and 96 current entries. A separate supplied DFlash image contained
the supported 0033 layout at record `0x00805670`, VIN `0x0080568C`. An in-memory
edit preserved its four-byte tail and every other record; the source stayed
unchanged. No private VIN, key, certificate or dump is included in test fixtures.
