"""Offline structured dump editing adapted from the supplied SRecordizerMac.

Input/output is a list of ``(physical_address, bytes)`` segments. No file, USB,
network, gap filling, relocation or target programming happens in this module.
Only factual format layouts are included; an optional caller-supplied Tesla
catalog supplies display names. No extracted vendor dictionary is bundled.

``analyze`` returns JSON-compatible tesla/vcsec/spc56 records, page state and
notes. ``edit`` returns a new segment list with unchanged addresses/lengths:

* parameter: record_id, value_hex (same payload length)
* spc56: record_id, text; normalization=preserve|lineFeedTerminated,
  padding_policy=preserve|remove
* vin: record_id or record_ids, vin (selected existing VIN fields only)
* key: record_id, slot (0..19), key_hex (P-256 public point or null to clear)
* certificate: record_id, value_hex (exact 1002-byte opaque blob)

Optional expected_snapshot / expected_snapshots guards selected raw records.
The caller must additionally bind edits to an immutable image revision/hash.
CRC-valid Tesla records remain format candidates, not firmware identification.
Certificate editing does not verify, generate, or repair cryptographic signatures.
"""
from hashlib import sha256
import re


MAX_BYTES = 16 * 1024 * 1024
MAX_PARAMETERS = 4096
PAGE_SIZE = 0x4000
KNOWN_LAYOUTS = {
    3: {2: 22}, 5: {1: 1400}, 7: {1: 1002}, 8: {1: 34}, 9: {1: 421},
    10: {1: 500, 2: 80}, 12: {1: 80}, 13: {1: 64}, 14: {1: 2},
    15: {2: 4}, 16: {3: 5}, 17: {1: 105}, 18: {1: 160}, 19: {1: 4},
    20: {1: 1}, 21: {1: 20}, 22: {1: 240}, 23: {1: 33}, 32: {1: 8},
    33: {1: 20}, 34: {1: 3}, 35: {1: 33}, 36: {1: 20}, 37: {1: 8},
    38: {1: 32}, 39: {1: 80}, 40: {1: 8}, 41: {1: 4}, 42: {1: 20},
    47: {1: 16}, 49: {1: 140, 2: 148}, 51: {1: 21}, 53: {1: 20}, 54: {1: 1},
}
VCSEC_NAMES = {3: 'Config', 4: 'Phone keys counter', 5: 'Key slots',
               7: 'Certificate (VIN)', 8: 'Auth data', 9: 'Per-slot secrets',
               10: 'Settings', 12: 'Limits', 13: 'Shared secret', 17: 'Crypto data',
               18: 'Timestamps', 21: 'Config2', 23: 'Counters', 35: 'Features', 37: 'Version',
               0x33: 'VIN data (0x0033)'}
SPC56_ZONES = ((0x18000, 0x1C3F0, 'firmware.rc'), (0x1C400, 0x1FFF0, 'internal.dat'))


def _crc_table(poly, width):
    mask, top = (1 << width) - 1, 1 << (width - 1)
    table = []
    for value in range(256):
        value <<= width - 8
        for _ in range(8):
            value = ((value << 1) ^ (poly if value & top else 0)) & mask
        table.append(value)
    return tuple(table)


_CRC8 = _crc_table(0x2F, 8)
_CRC32 = _crc_table(0x04C11DB7, 32)


def crc8(data):
    value = 0xFF
    for byte in data:
        value = _CRC8[value ^ byte]
    return value


def crc32(data):
    value = 0
    for byte in data:
        value = ((value << 8) & 0xFFFFFFFF) ^ _CRC32[(value >> 24) ^ byte]
    return value


def _hex(value, label='value_hex'):
    if not isinstance(value, str) or len(value) > MAX_BYTES * 3:
        raise ValueError(f'{label} must be hexadecimal text')
    compact = ''.join(value.split())
    if len(compact) % 2 or re.fullmatch(r'[0-9a-fA-F]*', compact) is None:
        raise ValueError(f'{label} must contain complete hexadecimal bytes')
    return bytes.fromhex(compact)


def _ascii(data):
    return ''.join(chr(b) if 32 <= b <= 126 or b in (9, 10, 13) else '.' for b in data)


def _printable(data):
    return all(32 <= b <= 126 or b in (9, 10, 13) for b in data)


def _vin(data):
    return len(data) == 17 and all(48 <= b <= 57 or 65 <= b <= 90 and b not in (73, 79, 81) for b in data)


def valid_public_key(data):
    """Validate an uncompressed SEC1 P-256 point without optional dependencies."""
    if len(data) != 65 or data[0] != 4:
        return False
    p = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
    b = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
    x, y = int.from_bytes(data[1:33], 'big'), int.from_bytes(data[33:], 'big')
    return x < p and y < p and (y * y - (x * x * x - 3 * x + b)) % p == 0


class _Space:
    def __init__(self, segments):
        self.parts = []
        total = 0
        for address, data in segments:
            if type(address) is not int or not isinstance(data, (bytes, bytearray, memoryview)):
                raise ValueError('segments require integer addresses and byte contents')
            if address < 0 or not len(data) or address + len(data) > 0x100000000:
                raise ValueError('invalid or empty segment range')
            total += len(data)
            if total > MAX_BYTES:
                raise ValueError('structured editor image exceeds 16 MiB')
            self.parts.append((address, bytearray(data)))
        self.order = sorted(range(len(self.parts)), key=lambda i: self.parts[i][0])
        previous = 0
        for i in self.order:
            address, data = self.parts[i]
            if address < previous:
                raise ValueError('segments overlap')
            previous = address + len(data)

    def segments(self):
        result = []
        for i in self.order:
            address, data = self.parts[i]
            if result and result[-1][0] + len(result[-1][1]) == address:
                result[-1][1].extend(data)
            else:
                result.append((address, bytearray(data)))
        return [(a, bytes(d)) for a, d in result]

    def plan(self, address, size):
        if type(address) is not int or type(size) is not int or address < 0 or size < 0 or address + size > 0x100000000:
            raise ValueError('invalid address span')
        end, cursor, result = address + size, address, []
        for i in self.order:
            base, data = self.parts[i]
            lo, hi = max(cursor, base), min(end, base + len(data))
            if lo >= hi:
                continue
            if lo != cursor:
                raise ValueError('structured field crosses missing bytes')
            result.append((i, lo - base, hi - base))
            cursor = hi
            if cursor == end:
                break
        if cursor != end:
            raise ValueError('structured field is outside the image')
        return result

    def read(self, address, size):
        return b''.join(self.parts[i][1][lo:hi] for i, lo, hi in self.plan(address, size))

    def write(self, address, data):
        plan = self.plan(address, len(data))
        offset = 0
        for i, lo, hi in plan:
            self.parts[i][1][lo:hi] = data[offset:offset + hi - lo]
            offset += hi - lo

    def result(self):
        return [(a, bytes(d)) for a, d in self.parts]


def validate_catalog(catalog):
    """Validate an optional locally supplied display dictionary; never execute it."""
    if catalog is None:
        return []
    if not isinstance(catalog, list) or len(catalog) > 4096:
        raise ValueError('catalog must be a list of at most 4096 options')
    for item in catalog:
        if (not isinstance(item, dict) or type(item.get('id')) is not int or not 0 <= item['id'] <= 65535
                or not isinstance(item.get('name'), str) or len(item['name']) > 1024
                or not isinstance(item.get('type', ''), str) or len(item.get('type', '')) > 64
                or not isinstance(item.get('description', ''), str) or len(item.get('description', '')) > 8192):
            raise ValueError('invalid catalog option')
        values = item.get('values')
        if values is not None and (not isinstance(values, dict) or len(values) > 4096 or
                                  any(not isinstance(k, str) or not isinstance(v, str) or len(k) > 1024 or len(v) > 4096
                                      for k, v in values.items())):
            raise ValueError('invalid catalog values')
    return catalog


def _describe(parameter_id, marker, value, catalog):
    if marker == 0x14:
        return f'ECU/version block 0x{parameter_id:02X}', 'Marker 14 · raw data'
    if marker == 0x15:
        return f'Block 15 / 0x{parameter_id:02X}', 'Marker 15 · raw data'
    option = next((o for o in catalog if o['id'] == parameter_id), None)
    if option is None:
        if parameter_id == 0 and _vin(value):
            return 'VIN', 'Existing 17-byte VIN field · CRC8 candidate'
        return f'Configuration 0x{parameter_id:02X}', 'Unknown configuration field · CRC8 candidate'
    decoded = value.hex().upper()
    if option.get('type') == 'string' and all(b == 0 or 32 <= b <= 126 for b in value):
        decoded = value.split(b'\x00', 1)[0].decode('ascii')
    elif option.get('type') == 'enum' and isinstance(option.get('values'), dict):
        text = value.decode('ascii', errors='replace')
        numeric = str(int.from_bytes(value, 'big')) if len(value) <= 8 else ''
        decoded = option['values'].get(text, option['values'].get(numeric, decoded))
    return option['name'], ' · '.join(filter(None, (option.get('description', ''), decoded)))


def _record(family, address, parameter_id, marker, value, raw, stored, computed, name, detail, **extra):
    return dict(id=f'{family}-{address:08X}', family=family, address=address,
                parameter_id=parameter_id, marker=marker, value_address=address + (4 if family == 'tesla' else 28),
                value_hex=value.hex().upper(), ascii=_ascii(value), name=name, detail=detail,
                byte_count=len(value), editable=True, state='candidate', checksum_valid=stored == computed,
                stored_checksum=stored, calculated_checksum=computed, snapshot=sha256(raw).hexdigest(),
                context=None, is_vin=False, vin=None, is_key_container=False, is_certificate=False, **extra)


def _scan_tesla(segments, catalog):
    records, truncated = [], False
    for address, data in segments:
        offset = 0
        while offset + 4 < len(data):
            length, marker = data[offset + 1:offset + 3]
            if not 1 <= length <= 150 or marker not in (0, 0x14, 0x15) or offset + 4 + length > len(data):
                offset += 1
                continue
            raw = data[offset:offset + 4 + length]
            checksum = crc8(raw[1:])
            if raw[0] != checksum:
                offset += 1
                continue
            if len(records) >= MAX_PARAMETERS:
                return records, True
            parameter_id, value = raw[3], raw[4:]
            name, detail = _describe(parameter_id, marker, value, catalog)
            record = _record('tesla', address + offset, parameter_id, marker, value, raw, raw[0], checksum, name, detail)
            record['is_vin'] = marker == 0 and parameter_id == 0 and _vin(value)
            if record['is_vin']:
                record['vin'] = value.decode('ascii')
                record['vin_offset'] = 0
            records.append(record)
            offset += len(raw)
    return records, truncated


def _u16(data, offset):
    return int.from_bytes(data[offset:offset + 2], 'big')


def _u32(data, offset):
    return int.from_bytes(data[offset:offset + 4], 'big')


def _inspect_journal(address, data):
    pages, entries = [], []
    for offset in range(0, len(data), PAGE_SIZE):
        page = data[offset:offset + PAGE_SIZE]
        counter = _u32(page, 0x10)
        blank_body = page[0x40:] == b'\xff' * (PAGE_SIZE - 0x40)
        if _u32(page, 0x20) != 0xFFFFFFFF:
            state = 5
        elif counter == 0xFFFFFFFF:
            state = 0 if page == b'\xff' * PAGE_SIZE else 4
        elif _u32(page, 0) != 0xFFFFFFFF:
            state = 2
        elif blank_body and _u32(page, 0x30) == 0xFFFFFFFF:
            state = 1
        elif not blank_body:
            state = 6 if _u32(page, 0x30) != 0xFFFFFFFF else 3
        else:
            state = 4
        pages.append(dict(address=address + offset, offset=offset, state=state, counter=counter, selected=False, issue=None))
    active = [i for i, p in enumerate(pages) if p['state'] == 2]
    selected, selection_issue = None, None
    if not active:
        selection_issue = 'No active VCSEC page could be selected.'
    elif any(p['state'] not in (0, 1, 2) for p in pages):
        selection_issue = 'Transitional or invalid VCSEC page state; current selection is uncertain.'
    elif len(active) == 1:
        selected = active[0]
    else:
        ends = [i for i in active if pages[(i + 1) % len(pages)]['state'] != 2]
        if len(ends) == 1:
            selected = ends[0]
        else:
            selection_issue = 'All-active or disjoint active VCSEC page runs; selection is ambiguous.'
    for index, page in enumerate(pages):
        if page['state'] in (0, 1):
            continue
        offset, end = page['offset'] + 0x40, page['offset'] + PAGE_SIZE
        while offset + 32 <= end:
            if data[offset:offset + 32] == b'\xff' * 32:
                if data[offset:end] != b'\xff' * (end - offset):
                    page['issue'] = 'Non-erased data follows an erased journal entry.'
                break
            status = _u32(data, offset)
            app_format, revision, size = (_u16(data, offset + n) for n in (8, 10, 12))
            record_id, declared = _u16(data, offset + 16), _u16(data, offset + 18)
            span = max(32, (size + 28 + 15) & ~15)
            if status not in (0xFFFF0000, 0) or declared != size + 16 or offset + span > end:
                page['issue'] = f'Torn or malformed journal entry at 0x{address + offset:08X}.'
                break
            raw = data[offset:offset + 28 + size]
            entries.append(dict(offset=offset, page=index, status=status, format=app_format, revision=revision,
                                record_id=record_id, raw=raw, value=raw[28:], stored=_u32(raw, 20), computed=crc32(raw[28:])))
            offset += span
        if offset + 32 > end and offset < end and data[offset:end] != b'\xff' * (end - offset):
            page['issue'] = 'Incomplete journal tail.'
    latest = {e['record_id']: i for i, e in enumerate(entries) if e['page'] == selected}
    records = []
    for index, e in enumerate(entries):
        state = ('deleted' if e['status'] == 0 else 'uncertain' if selected is None else
                 'current' if latest.get(e['record_id']) == index else 'history')
        issue = None
        if state != 'current':
            issue = selection_issue or 'Only current journal records can be edited.'
        elif pages[e['page']]['issue']:
            issue = pages[e['page']]['issue']
        elif e['stored'] != e['computed']:
            issue = 'Current record CRC is invalid; no historical fallback.'
        elif e['format'] != 1:
            issue = 'Unsupported VCSEC application format.'
        elif KNOWN_LAYOUTS.get(e['record_id'], {}).get(e['revision']) != len(e['value']):
            issue = 'Payload size or schema revision is not in the verified VCSEC profiles; read-only.'
        record_address = address + e['offset']
        detail = f"{state} · schema {e['revision']} · {len(e['value'])} bytes · CRC32 BE"
        if issue:
            detail += ' · ' + issue
        record = _record('vcsec', record_address, e['record_id'], None, e['value'], e['raw'], e['stored'], e['computed'],
                         VCSEC_NAMES.get(e['record_id'], f"VCSEC record 0x{e['record_id']:04X}"), detail)
        record.update(state=state, editable=issue is None, context=dict(image_address=address, page_address=pages[e['page']]['address'],
                      physical_address=record_address, record_id=e['record_id'], application_format=e['format'],
                      schema_revision=e['revision'], state=state, issue=issue, editable=issue is None))
        record['is_key_container'] = e['record_id'] == 5 and len(e['value']) == 1400 and e['format'] == 1 and e['revision'] == 1
        record['is_certificate'] = e['record_id'] == 7 and len(e['value']) == 1002 and e['format'] == 1 and e['revision'] == 1
        if record['is_key_container']:
            record['key_slots'] = _key_slots(e['value'])
        if record['is_certificate']:
            record['certificate'] = _certificate_info(e['value'])
            record['vin'] = record['certificate']['vin']
            record['is_vin'] = record['editable'] and record['certificate']['vin'] is not None
        # Supplied ID0x0033 schema1 dump: 17 ASCII VIN bytes followed by four
        # opaque bytes, not a certificate or a leading length field. Preserve
        # that tail byte-for-byte; its meaning is not inferred from one dump.
        if (e['record_id'] == 0x33 and e['format'] == 1 and e['revision'] == 1
                and len(e['value']) == 21 and _vin(e['value'][:17])):
            record.update(vin=e['value'][:17].decode('ascii'), vin_offset=0,
                          vin_length=17, vin_trailing_bytes=4, is_vin=record['editable'])
            record['detail'] += ' · VIN at payload +0; four trailing bytes preserved'
        records.append(record)
    notes = sorted(set(([selection_issue] if selection_issue else []) + [p['issue'] for p in pages if p['issue']]))
    names = {0: 'erased', 1: 'prepared', 2: 'active', 3: 'transitional', 5: 'invalidated', 6: 'transitional'}
    page_info = [dict(address=p['address'], state=names.get(p['state'], 'invalid'), counter=p['counter'], selected=i == selected,
                      issue=p['issue'] or selection_issue) for i, p in enumerate(pages)]
    return records, page_info, notes


def _key_slots(value):
    slots = []
    for index in range(20):
        raw = value[index * 70:(index + 1) * 70]
        occupied = raw[4] != 0
        key = raw[5:] if occupied and raw[:4] == b'\x00\x00\x00A' and valid_public_key(raw[5:]) else None
        slots.append(dict(slot=index, occupied=occupied, key_hex=key.hex().upper() if key else None,
                          status='65-byte key' if key else 'Unknown slot format — preserved' if occupied else 'Empty'))
    return slots


def _parse_der(data):
    def node(start, limit):
        if start < 0 or start + 2 > limit or limit > len(data):
            raise ValueError('Truncated DER field')
        tag, first = data[start:start + 2]
        if tag & 31 == 31:
            raise ValueError('Unsupported DER tag')
        cursor, length = start + 2, first
        if first & 128:
            count = first & 127
            if not 1 <= count <= 4 or cursor + count > limit or data[cursor] == 0:
                raise ValueError('Invalid DER length')
            length = int.from_bytes(data[cursor:cursor + count], 'big')
            cursor += count
            if length < 128:
                raise ValueError('Non-canonical DER length')
        if length > limit - cursor:
            raise ValueError('DER field exceeds its container')
        return tag, cursor, cursor + length
    root = node(0, len(data))
    if root[0] != 0x30:
        raise ValueError('Certificate is not a DER sequence')
    tbs = node(root[1], root[2])
    algorithm = node(tbs[2], root[2])
    signature = node(algorithm[2], root[2])
    if (tbs[0], algorithm[0], signature[0]) != (0x30, 0x30, 3) or signature[2] != root[2]:
        raise ValueError('Unsupported DER certificate structure')
    count = 0
    def children(parent):
        nonlocal count
        position, end, result = parent[1], parent[2], []
        while position < end:
            count += 1
            if count > 4096:
                raise ValueError('Too many DER fields')
            child = node(position, end)
            result.append(child)
            position = child[2]
        return result
    fields = children(tbs)
    base = int(bool(fields and fields[0][0] == 0xA0))
    if len(fields) < base + 6 or fields[base][0] != 2 or any(f[0] != 0x30 for f in fields[base + 1:base + 6]):
        raise ValueError('Unsupported TBSCertificate fields')
    validity = children(fields[base + 3])
    if len(validity) != 2 or any(f[0] not in (0x17, 0x18) for f in validity):
        raise ValueError('Unsupported certificate validity fields')
    vins = []
    for rdn in children(fields[base + 4]):
        if rdn[0] != 0x31:
            raise ValueError('Unsupported certificate subject')
        for attribute in children(rdn):
            if attribute[0] != 0x30:
                raise ValueError('Unsupported subject attribute')
            pair = children(attribute)
            if len(pair) != 2 or pair[0][0] != 6:
                raise ValueError('Invalid subject attribute')
            value = data[pair[1][1]:pair[1][2]]
            if data[pair[0][1]:pair[0][2]] == b'\x55\x04\x03' and pair[1][0] == 0x0C and _vin(value):
                vins.append((pair[1][1], pair[1][2]))
    return root[2], vins


def _certificate_info(value):
    try:
        root_end, ranges = _parse_der(value)
        vin = value[slice(*ranges[0])].decode('ascii') if len(ranges) == 1 else None
    except ValueError:
        root_end, vin = None, None
    return dict(byte_count=len(value), der_byte_count=root_end, vin=vin, signature_verified=False,
                summary=('Opaque certificate blob. DER structure not recognized; signature not verified.' if root_end is None else
                         f'DER certificate: {root_end} bytes; blob: {len(value)} bytes. Signature not verified.'))


def _scan_spc56(space):
    records = []
    for address, end, name in SPC56_ZONES:
        try:
            header = space.read(address, 4)
            count = _u16(header, 2)
            if header[:2] != b'\x00\x00' or address + 4 + count > end + 1:
                continue
            raw = space.read(address, 4 + count)
            text = raw[4:].rstrip(b'\xff')
            padding = count - len(text)
            if padding and not text or not _printable(text):
                continue
            warnings = ([f'Declared size includes {padding} trailing FF bytes. Text view excludes them; saving preserves them unless explicitly removed.']
                        if padding else [])
            records.append(dict(id=f'spc56-{address:08X}', family='spc56', address=address, zone_end=end, name=name,
                                text=text.decode('ascii'), byte_count=count, text_byte_count=len(text), padding_byte_count=padding,
                                warnings=warnings, editable=True, state='text', snapshot=sha256(raw).hexdigest(),
                                value_hex=raw[4:].hex().upper(), value_address=address + 4, detail=' · '.join(warnings)))
        except ValueError:
            continue
    return records


def analyze(segments, *, force_tesla=False, catalog=None):
    """Parse supported file structures; all returned values are JSON-compatible."""
    if type(force_tesla) is not bool:
        raise ValueError('force_tesla must be boolean')
    catalog = validate_catalog(catalog)
    space = _Space(segments)
    joined = space.segments()
    vcsec, pages, notes = [], [], []
    truncated = False
    for address, data in joined:
        if len(data) not in (PAGE_SIZE, 4 * PAGE_SIZE) or address % PAGE_SIZE:
            continue
        records, page_info, issues = _inspect_journal(address, data)
        vcsec.extend(records)
        pages.extend(page_info)
        notes.extend(issues)
    spc56 = _scan_spc56(space)
    if not force_tesla and (spc56 or vcsec):
        tesla = []
        hint = ('SPC56 text blocks found; use firmware.rc/internal.dat.' if spc56 else
                'VCSEC journal found; use current journal records.')
    else:
        tesla, truncated = _scan_tesla(joined, catalog)
        hint = 'CRC8 candidates do not identify a firmware schema. Check field meaning before editing.'
    if truncated:
        notes.append(f'Tesla candidate list limited to {MAX_PARAMETERS} records; remaining bytes were not interpreted.')
    return dict(tesla=tesla, vcsec=vcsec, spc56=spc56, pages=pages, notes=sorted(set(notes)), tesla_hint=hint, truncated=truncated)


def _update_parameter(space, record, value):
    if not record['editable']:
        raise ValueError((record.get('context') or {}).get('issue') or 'Selected record is read-only')
    if len(value) != record['byte_count']:
        raise ValueError(f"Parameter length must remain {record['byte_count']} bytes")
    prefix = 4 if record['family'] == 'tesla' else 28
    raw = bytearray(space.read(record['address'], prefix + len(value)))
    if sha256(raw).hexdigest() != record['snapshot']:
        raise ValueError('Parameter bytes changed; scan again before editing')
    raw[prefix:] = value
    if record['family'] == 'tesla':
        raw[0] = crc8(raw[1:])
    else:
        raw[20:24] = crc32(value).to_bytes(4, 'big')
    space.write(record['address'], raw)


def _update_text(space, record, request):
    text = request.get('text')
    normalization = request.get('normalization', 'preserve')
    policy = request.get('padding_policy', 'preserve')
    if not isinstance(text, str) or normalization not in ('preserve', 'lineFeedTerminated') or policy not in ('preserve', 'remove'):
        raise ValueError('Invalid SPC56 text or normalization/padding policy')
    if normalization == 'lineFeedTerminated':
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        if not text.endswith('\n'):
            text += '\n'
    try:
        value = text.encode('ascii')
    except UnicodeEncodeError as exc:
        raise ValueError('SPC56 text must be printable ASCII plus tab/newline') from exc
    if not _printable(value):
        raise ValueError('SPC56 text must be printable ASCII plus tab/newline')
    padding = record['padding_byte_count'] if policy == 'preserve' else 0
    value += b'\xff' * padding
    if len(value) > 65535 or record['address'] + 4 + len(value) > record['zone_end'] + 1:
        raise ValueError('SPC56 content exceeds its reserved zone')
    old_size = record['byte_count'] + 4
    current = space.read(record['address'], max(old_size, len(value) + 4))
    if sha256(current[:old_size]).hexdigest() != record['snapshot']:
        raise ValueError('SPC56 bytes changed; scan again before editing')
    if current[old_size:] != b'\xff' * (len(current) - old_size):
        raise ValueError('SPC56 growth would overwrite non-erased bytes')
    replacement = b'\x00\x00' + len(value).to_bytes(2, 'big') + value
    replacement += b'\xff' * (len(current) - len(replacement))
    space.write(record['address'], replacement)


def edit(segments, request, *, catalog=None):
    """Apply one structured action transactionally to a copy of the image."""
    if not isinstance(request, dict):
        raise ValueError('Expected a structured edit object')
    operation = request.get('operation')
    if operation not in ('parameter', 'spc56', 'vin', 'key', 'certificate'):
        raise ValueError('Unsupported structured edit operation')
    space = _Space(segments)
    parsed = analyze(space.result(), force_tesla=True, catalog=catalog)
    records = {r['id']: r for section in ('tesla', 'vcsec', 'spc56') for r in parsed[section]}
    selected_ids = request.get('record_ids') if operation == 'vin' and 'record_ids' in request else [request.get('record_id')]
    if (not isinstance(selected_ids, list) or not selected_ids or
            any(not isinstance(i, str) for i in selected_ids) or len(set(selected_ids)) != len(selected_ids)):
        raise ValueError('Select distinct existing record IDs')
    selected = []
    for record_id in selected_ids:
        record = records.get(record_id)
        if record is None:
            raise ValueError('Selected structured record no longer exists; scan again')
        expected = request.get('expected_snapshot')
        if 'expected_snapshots' in request:
            if not isinstance(request['expected_snapshots'], dict):
                raise ValueError('expected_snapshots must be an object')
            expected = request['expected_snapshots'].get(record_id, expected)
        if expected is not None and expected != record['snapshot']:
            raise ValueError('Selected record changed; scan again before editing')
        if not record['editable']:
            raise ValueError((record.get('context') or {}).get('issue') or 'Selected record is read-only')
        selected.append(record)
    record = selected[0]
    if operation == 'spc56':
        if record['family'] != 'spc56':
            raise ValueError('Select a supported SPC56 text block')
        _update_text(space, record, request)
    elif operation == 'parameter':
        if record['family'] not in ('tesla', 'vcsec'):
            raise ValueError('Select a Tesla or VCSEC parameter')
        _update_parameter(space, record, _hex(request.get('value_hex')))
    elif operation == 'certificate':
        if not record.get('is_certificate'):
            raise ValueError('Select VCSEC certificate record ID 7, schema 1, 1002 bytes')
        _update_parameter(space, record, _hex(request.get('value_hex')))
    elif operation == 'vin':
        vin = request.get('vin')
        if not isinstance(vin, str):
            raise ValueError('VIN must contain 17 uppercase ASCII letters/digits, excluding I, O and Q')
        value = vin.encode('utf-8')
        if not _vin(value):
            raise ValueError('VIN must contain 17 uppercase ASCII letters/digits, excluding I, O and Q')
        for record in selected:
            if not record.get('is_vin'):
                raise ValueError('Select an existing supported VIN field')
            if record['family'] == 'tesla':
                _update_parameter(space, record, value)
            elif record['parameter_id'] == 0x33:
                old = bytearray.fromhex(record['value_hex'])
                old[:17] = value  # Exact schema checked by the fresh scan above.
                _update_parameter(space, record, old)
            else:
                old = bytearray.fromhex(record['value_hex'])
                _, ranges = _parse_der(old)
                if len(ranges) != 1:
                    raise ValueError('Certificate must contain exactly one supported DER VIN field')
                lo, hi = ranges[0]
                old[lo:hi] = value
                _update_parameter(space, record, old)
    elif operation == 'key':
        if not record.get('is_key_container'):
            raise ValueError('Select VCSEC key record ID 5, schema 1, 1400 bytes')
        slot = request.get('slot')
        if type(slot) is not int or not 0 <= slot < 20:
            raise ValueError('Key slot must be 0..19')
        if 'key_hex' not in request:
            raise ValueError('key_hex is required; use null to clear a slot')
        key = None if request['key_hex'] is None else _hex(request['key_hex'], 'key_hex')
        if key is not None:
            if not valid_public_key(key):
                raise ValueError('Key must be a valid 65-byte uncompressed P-256 public point')
            if any(s['slot'] != slot and s['key_hex'] == key.hex().upper() for s in record['key_slots']):
                raise ValueError('This public key already exists in another current slot')
            selected_slot = record['key_slots'][slot]
            if selected_slot['occupied'] and selected_slot['key_hex'] is None:
                raise ValueError('Occupied slot has unknown format; clear it explicitly before replacement')
        secret = None
        if key is None:
            secret = next((r for r in parsed['vcsec'] if r['parameter_id'] == 9 and r['state'] == 'current'
                           and r['context']['image_address'] == record['context']['image_address']), None)
            if secret is not None and (not secret['editable'] or secret['byte_count'] != 421 or secret['context']['schema_revision'] != 1):
                raise ValueError('Current per-slot secret record is corrupt or unsupported; no changes made')
        payload = bytearray.fromhex(record['value_hex'])
        payload[slot * 70:(slot + 1) * 70] = b'\x00' * 70 if key is None else b'\x00\x00\x00A\x01' + key
        _update_parameter(space, record, payload)
        if secret is not None:
            payload = bytearray.fromhex(secret['value_hex'])
            payload[slot * 16:(slot + 1) * 16] = b'\x00' * 16
            _update_parameter(space, secret, payload)
    return space.result()
