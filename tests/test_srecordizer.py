"""Synthetic format regression tests; no customer VINs, keys, certificates or USB.

G and 2G below are public P-256 generator points. Journal construction uses
an independent bitwise CRC implementation and explicit physical framing.
"""
import json
import unittest
from unittest.mock import patch

from spc5tool.srecordizer import analyze, edit, crc8, crc32, valid_public_key, validate_catalog


VIN = '5YJ3E1EA7KF000001'
NEW_VIN = '5YJ3E1EA7KF000002'
GENERATOR = bytes.fromhex('04'
    '6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296'
    '4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5')
SECOND = bytes.fromhex('04'
    '7cf27b188d034f7e8a52380304b51ac3c08969e277f21b35a60b48fc47669978'
    '07775510db8ed040293d9ac69f7430dbba7dade63ce982299e04b79d227873d1')


def reference_crc(data, width=32):
    result = 0 if width == 32 else 255
    poly = 0x04C11DB7 if width == 32 else 0x2F
    for byte in data:
        result ^= byte << (width - 8)
        for _ in range(8):
            result = ((result << 1) ^ (poly if result & (1 << (width - 1)) else 0)) & ((1 << width) - 1)
    return result


def tesla(value, marker=0, parameter_id=0):
    body = bytes((len(value), marker, parameter_id)) + value
    return bytes((reference_crc(body, 8),)) + body


def entry(parameter_id, value, revision=1, status=0xFFFF0000, bad_crc=False, app_format=1):
    header = (status.to_bytes(4, 'big') + b'\xff' * 4 + app_format.to_bytes(2, 'big') + revision.to_bytes(2, 'big')
              + len(value).to_bytes(2, 'big') + b'\xA1\xA2' + parameter_id.to_bytes(2, 'big')
              + (len(value) + 16).to_bytes(2, 'big') + (reference_crc(value) ^ int(bad_crc)).to_bytes(4, 'big')
              + b'\xB1\xB2\xB3\xB4')
    result = header + value
    return result + b'\xff' * (max(32, (len(result) + 15) & ~15) - len(result))


def journal(records=None, *, active=(0,), pages=4, address=0x800000):
    data = bytearray(b'\xff' * (0x4000 * pages))
    records = records or {}
    for page in range(pages):
        base = page * 0x4000
        data[base + 0x10:base + 0x14] = (page + 1).to_bytes(4, 'big')
        if page in active:
            data[base:base + 4] = b'\x00\x00\xff\xff'
            data[base + 0x30:base + 0x34] = b'\x00\x00\xff\xff'
        body = b''.join(records.get(page, []))
        assert len(body) <= 0x3FC0
        data[base + 0x40:base + 0x40 + len(body)] = body
    return [(address, bytes(data))]


def keyslot(key=GENERATOR, flag=1):
    return len(key).to_bytes(4, 'big') + bytes((flag,)) + key


def certificate(*, duplicate=False):
    def tlv(tag, body):
        return bytes((tag, len(body))) + body
    attribute = tlv(0x30, b'\x06\x03\x55\x04\x03' + tlv(0x0C, VIN.encode()))
    rdn = tlv(0x31, attribute)
    subject = tlv(0x30, rdn * (2 if duplicate else 1))
    date = tlv(0x17, b'250101000000Z')
    tbs = tlv(0x30, b'\x02\x01\x01\x30\x00\x30\x00' + tlv(0x30, date * 2) + subject + b'\x30\x00')
    blob = tlv(0x30, tbs + b'\x30\x00\x03\x02\x00\x42') + b'\xA5\xA6\xA7'
    return blob + b'\x00' * (1002 - len(blob))


def current(segments, parameter_id):
    return next(r for r in analyze(segments)['vcsec'] if r['parameter_id'] == parameter_id and r['state'] == 'current')


class SrecordizerTests(unittest.TestCase):
    def test_crc_known_answers_and_independent_vectors(self):
        self.assertEqual(crc8(b''), 0xFF)
        self.assertEqual(crc8(b'123456789'), 0x20)
        self.assertEqual(crc32(b''), 0)
        self.assertEqual(crc32(b'123456789'), 0x89A1897F)
        for length in (1, 20, 150, 1400):
            value = bytes(n % 256 for n in range(length))
            self.assertEqual(crc8(value), reference_crc(value, 8))
            self.assertEqual(crc32(value), reference_crc(value))

    def test_fixed_tesla_frame_across_contiguous_segments_and_gap(self):
        frame = bytes.fromhex('AD11000035594A3345314541374B46303030303031')
        segments = [(0x1000, frame[:7]), (0x1007, frame[7:])]
        result = analyze(segments)
        record, = result['tesla']
        self.assertEqual(record['name'], 'VIN')
        self.assertTrue(record['is_vin'])
        self.assertEqual(record['address'], 0x1000)
        self.assertEqual(record['value_address'], 0x1004)
        updated = edit(segments, dict(operation='vin', record_id=record['id'], vin=NEW_VIN, expected_snapshot=record['snapshot']))
        self.assertEqual([(a, len(v)) for a, v in updated], [(a, len(v)) for a, v in segments])
        self.assertEqual(analyze(updated)['tesla'][0]['ascii'], NEW_VIN)
        self.assertEqual(segments, [(0x1000, frame[:7]), (0x1007, frame[7:])])
        self.assertEqual(analyze([(0x1000, frame[:7]), (0x1008, frame[7:])])['tesla'], [])
        json.dumps(result)

    def test_tesla_checksum_marker_length_and_stale_rejection(self):
        frame = tesla(b'ABC')
        for bad in (bytes((frame[0] ^ 1,)) + frame[1:], tesla(b'ABC', marker=0x99), frame[:-1]):
            self.assertEqual(analyze([(0, bad)])['tesla'], [])
        source = [(0, frame)]
        record = analyze(source)['tesla'][0]
        with self.assertRaisesRegex(ValueError, 'length'):
            edit(source, dict(operation='parameter', record_id=record['id'], value_hex='FF'))
        updated = edit(source, dict(operation='parameter', record_id=record['id'], value_hex='44 45 46'))
        self.assertEqual(analyze(updated)['tesla'][0]['ascii'], 'DEF')
        with self.assertRaisesRegex(ValueError, 'changed'):
            edit(updated, dict(operation='parameter', record_id=record['id'], expected_snapshot=record['snapshot'], value_hex='414243'))

    def test_selected_vin_batch_is_atomic_and_explicit(self):
        source = [(0, tesla(VIN.encode()) + b'\xff' * 3 + tesla(VIN.encode()))]
        records = analyze(source)['tesla']
        updated = edit(source, dict(operation='vin', record_ids=[r['id'] for r in records], vin=NEW_VIN))
        self.assertEqual([r['ascii'] for r in analyze(updated)['tesla']], [NEW_VIN, NEW_VIN])
        for request in (dict(record_ids=[records[0]['id']] * 2, vin=NEW_VIN),
                        dict(record_ids=[r['id'] for r in records], vin='invalid'),
                        dict(record_ids=[r['id'] for r in records], vin=NEW_VIN,
                             expected_snapshots={records[-1]['id']: '0' * 64})):
            with self.assertRaises(ValueError):
                edit(source, dict(operation='vin', **request))
        self.assertEqual([r['ascii'] for r in analyze(source)['tesla']], [VIN, VIN])

    def test_catalog_is_optional_data_and_candidate_scan_is_bounded(self):
        source = [(0x1000, tesla(b'\x02', parameter_id=12))]
        catalog = [dict(id=12, type='enum', name='Synthetic switch', values={'2': 'Selected'}, description='Fixture')]
        record = analyze(source, catalog=catalog)['tesla'][0]
        self.assertEqual(record['name'], 'Synthetic switch')
        self.assertEqual(record['detail'], 'Fixture · Selected')
        with self.assertRaises(ValueError):
            validate_catalog([dict(id=True, name='invalid')])
        with patch('spc5tool.srecordizer.MAX_PARAMETERS', 2):
            result = analyze([(0, tesla(b'X') * 4)])
        self.assertEqual(len(result['tesla']), 2)
        self.assertTrue(result['truncated'])
        self.assertTrue(result['notes'])

    def test_vcsec_physical_ids_crc_endianness_and_header_preservation(self):
        raw = entry(8, bytes(range(34)))
        source = journal({0: [raw]})
        record = current(source, 8)
        self.assertEqual(record['address'], 0x800040)
        self.assertEqual(record['value_address'], 0x80005C)
        updated = edit(source, dict(operation='parameter', record_id=record['id'], value_hex='7A' * 34))
        new = updated[0][1][0x40:0x40 + len(raw)]
        self.assertEqual(new[:20], raw[:20])
        self.assertEqual(new[20:24], reference_crc(b'\x7A' * 34).to_bytes(4, 'big'))
        self.assertEqual(new[24:28], raw[24:28])
        self.assertEqual(new[62:], raw[62:])
        self.assertEqual(updated[0][1][:0x40], source[0][1][:0x40])
        self.assertTrue(current(updated, 8)['checksum_valid'])
        key = current(journal({0: [entry(5, b'\x00' * 1400)]}), 5)
        self.assertEqual(key['parameter_id'], 5)
        self.assertTrue(key['is_key_container'])

    def test_latest_bad_crc_and_tombstone_do_not_fall_back(self):
        for newer, state in ((entry(8, b'\x02' * 34), 'current'),
                             (entry(8, b'\x02' * 34, bad_crc=True), 'current'),
                             (entry(8, b'\x02' * 34, status=0), 'deleted')):
            source = journal({0: [entry(8, b'\x01' * 34), newer]})
            records = analyze(source)['vcsec']
            self.assertEqual([r['state'] for r in records], ['history', state])
            with self.assertRaises(ValueError):
                edit(source, dict(operation='parameter', record_id=records[0]['id'], value_hex='03' * 34))
            if state == 'deleted' or not records[1]['checksum_valid']:
                self.assertFalse(any(r['editable'] for r in records))
                with self.assertRaises(ValueError):
                    edit(source, dict(operation='parameter', record_id=records[1]['id'], value_hex='03' * 34))

    def test_page_ring_wrap_and_missing_id_never_falls_back_to_old_page(self):
        source = journal({0: [entry(8, b'\x01' * 34)], 3: [entry(5, b'\x00' * 1400)]}, active=(3, 0))
        result = analyze(source)
        self.assertEqual([p['address'] for p in result['pages'] if p['selected']], [0x800000])
        self.assertEqual([r['parameter_id'] for r in result['vcsec'] if r['state'] == 'current'], [8])
        self.assertEqual(next(r['state'] for r in result['vcsec'] if r['parameter_id'] == 5), 'history')

    def test_ambiguous_transitional_invalidated_and_torn_pages_are_readonly(self):
        cases = [journal({0: [entry(5, b'\x00' * 1400)]}, active=active) for active in ((0, 2), (0, 1, 2, 3))]
        cases.append(journal({0: [entry(5, b'\x00' * 1400)], 1: [entry(8, b'\x00' * 34)]}))
        data = bytearray(journal({0: [entry(5, b'\x00' * 1400)]})[0][1])
        data[0x20:0x24] = b'\x00' * 4
        cases.append([(0x800000, data)])
        torn = bytearray(entry(8, b'\x00' * 34))
        torn[12:14], torn[18:20] = b'\x3f\xff', b'\x40\x0f'
        cases.append(journal({0: [entry(5, b'\x00' * 1400), torn]}))
        for source in cases:
            result = analyze(source)
            self.assertTrue(result['notes'])
            self.assertTrue(result['vcsec'])
            self.assertFalse(any(r['editable'] for r in result['vcsec']))
            with self.assertRaises(ValueError):
                edit(source, dict(operation='key', record_id=result['vcsec'][0]['id'], slot=0, key_hex=GENERATOR.hex()))

    def test_erased_hole_followed_by_data_disables_current_record(self):
        first = entry(8, b'\x00' * 34)
        source = journal({0: [first, b'\xff' * 32, entry(5, b'\x00' * 1400)]})
        result = analyze(source)
        self.assertEqual(len(result['vcsec']), 1)
        self.assertFalse(result['vcsec'][0]['editable'])
        self.assertIn('Non-erased', result['notes'][0])

    def test_embedded_patterns_unknown_layout_and_revision_remain_readonly(self):
        source = journal({0: [entry(0xBEEF, entry(5, b'\x00' * 1400)),
                              entry(0x31, b'\x00' * 148, revision=2), entry(0x10, b'\x00' * 5, revision=3),
                              entry(5, b'\x00' * 1400, revision=2), entry(8, b'\x00' * 34, app_format=2)]})
        records = analyze(source)['vcsec']
        self.assertEqual([r['parameter_id'] for r in records], [0xBEEF, 0x31, 0x10, 5, 8])
        self.assertEqual([r['editable'] for r in records], [False, True, True, False, False])
        self.assertFalse(records[3]['is_key_container'])

    def test_complete_journal_required_and_contiguous_parts_are_accepted(self):
        for base in (0, 0x800000, 0x804000, 0x80C000):
            source = journal({0: [entry(5, b'\x00' * 1400)]}, pages=1, address=base)
            self.assertEqual(current(source, 5)['address'], base + 0x40)
            data = source[0][1]
            self.assertEqual(analyze([(base, data[:-1])])['vcsec'], [])
            self.assertEqual(analyze([(base + 1, data)])['vcsec'], [])
            split = [(base, data[:100]), (base + 100, data[100:])]
            self.assertEqual(current(split, 5)['address'], base + 0x40)
            changed = edit(split, dict(operation='key', record_id=current(split, 5)['id'], slot=0, key_hex=GENERATOR.hex()))
            self.assertEqual([(a, len(v)) for a, v in split], [(a, len(v)) for a, v in changed])

    def test_p256_validation_and_slot_write_preserve_unknowns(self):
        self.assertTrue(valid_public_key(GENERATOR))
        self.assertTrue(valid_public_key(SECOND))
        for invalid in (b'\x04' + b'\x00' * 64, b'\x04' + b'\xff' * 64, GENERATOR[:-1], b'\x02' + GENERATOR[1:]):
            self.assertFalse(valid_public_key(invalid))
        value = b'\xA5' * 70 + keyslot(SECOND, flag=7) + b'\x00' * 1260
        source = journal({0: [entry(5, value)]})
        record = current(source, 5)
        self.assertEqual(record['key_slots'][0]['status'], 'Unknown slot format — preserved')
        for slot, key in ((19, b'\x04' + b'\x00' * 64), (19, SECOND), (0, GENERATOR)):
            with self.assertRaises(ValueError):
                edit(source, dict(operation='key', record_id=record['id'], slot=slot, key_hex=key.hex()))
        updated = edit(source, dict(operation='key', record_id=record['id'], slot=19, key_hex=GENERATOR.hex()))
        new = current(updated, 5)
        self.assertEqual(bytes.fromhex(new['value_hex'])[:1330], value[:1330])
        self.assertEqual(new['key_slots'][19]['key_hex'], GENERATOR.hex().upper())
        self.assertTrue(new['checksum_valid'])

    def test_key_removal_updates_only_current_secret_atomically(self):
        keys = keyslot() + keyslot(SECOND) + b'\x00' * 1260
        secrets = b'\xA5' * 421
        source = journal({0: [entry(9, secrets), entry(5, keys), entry(9, secrets)]})
        record = current(source, 5)
        updated = edit(source, dict(operation='key', record_id=record['id'], slot=1, key_hex=None))
        secret = current(updated, 9)
        self.assertEqual(bytes.fromhex(secret['value_hex']), secrets[:16] + b'\x00' * 16 + secrets[32:])
        self.assertTrue(secret['checksum_valid'])
        self.assertEqual(bytes.fromhex(current(updated, 5)['value_hex'])[70:140], b'\x00' * 70)
        old_history = analyze(source)['vcsec'][0]
        new_history = analyze(updated)['vcsec'][0]
        self.assertEqual(old_history, new_history)
        for wrong in (entry(9, secrets, bad_crc=True), entry(9, secrets, revision=2), entry(9, secrets[:-1])):
            source = journal({0: [entry(5, keys), wrong]})
            with self.assertRaisesRegex(ValueError, 'secret'):
                edit(source, dict(operation='key', record_id=current(source, 5)['id'], slot=1, key_hex=None))
            self.assertEqual(bytes.fromhex(current(source, 5)['value_hex']), keys)

    def test_key_clear_is_explicit_and_rejects_invalid_slot_types(self):
        source = journal({0: [entry(5, b'\x00' * 1400)]})
        record = current(source, 5)
        for fields in (dict(slot=0), dict(slot=True, key_hex=None), dict(slot=-1, key_hex=None),
                       dict(slot=20, key_hex=None), dict(slot=0, key_hex='')):
            with self.assertRaises(ValueError):
                edit(source, dict(operation='key', record_id=record['id'], **fields))

    def test_certificate_vin_preserves_signature_and_opaque_tail(self):
        blob = certificate()
        source = journal({0: [entry(7, blob)]})
        record = current(source, 7)
        self.assertEqual(record['certificate']['vin'], VIN)
        self.assertFalse(record['certificate']['signature_verified'])
        updated = edit(source, dict(operation='vin', record_id=record['id'], vin=NEW_VIN))
        new = current(updated, 7)
        self.assertEqual(new['certificate']['vin'], NEW_VIN)
        self.assertEqual(bytes.fromhex(new['value_hex']), blob.replace(VIN.encode(), NEW_VIN.encode()))
        self.assertTrue(new['checksum_valid'])
        replacement = b'\xA5' * 1002
        imported = edit(source, dict(operation='certificate', record_id=record['id'], value_hex=replacement.hex()))
        result = current(imported, 7)
        self.assertIsNone(result['certificate']['der_byte_count'])
        self.assertEqual(bytes.fromhex(result['value_hex']), replacement)
        self.assertTrue(result['checksum_valid'])
        with self.assertRaises(ValueError):
            edit(source, dict(operation='certificate', record_id=record['id'], value_hex='3000'))

    def test_certificate_ambiguous_or_truncated_der_never_patches_vin(self):
        for value in (certificate(duplicate=True), b'\x30\x82\xff\xff' + b'\x00' * 998,
                      b'\x30\x80' + b'\x00' * 1000):
            source = journal({0: [entry(7, value)]})
            record = current(source, 7)
            self.assertIsNone(record['certificate']['vin'])
            self.assertFalse(record['is_vin'])
            with self.assertRaises(ValueError):
                edit(source, dict(operation='vin', record_id=record['id'], vin=NEW_VIN))
            self.assertEqual(bytes.fromhex(current(source, 7)['value_hex']), value)

    def test_vcsec_0033_vin_updates_only_selected_current_payload_and_crc(self):
        trailer = bytes.fromhex('A5F0017E')  # Synthetic opaque tail, not a length.
        value = VIN.encode() + trailer
        history = entry(0x33, value)
        source = journal({0: [history, entry(0x33, value), entry(8, b'\x7A' * 34)]})
        record = current(source, 0x33)
        self.assertTrue(record['is_vin'])
        self.assertEqual(record['vin'], VIN)
        self.assertEqual(record['vin_offset'], 0)
        self.assertEqual(record['vin_trailing_bytes'], 4)
        self.assertEqual(record['context']['application_format'], 1)
        self.assertEqual(record['context']['schema_revision'], 1)
        updated = edit(source, dict(operation='vin', record_id=record['id'], vin=NEW_VIN,
                                   expected_snapshot=record['snapshot']))
        changed = current(updated, 0x33)
        self.assertEqual(changed['vin'], NEW_VIN)
        self.assertEqual(bytes.fromhex(changed['value_hex']), NEW_VIN.encode() + trailer)
        self.assertTrue(changed['checksum_valid'])
        self.assertEqual(changed['stored_checksum'], reference_crc(NEW_VIN.encode() + trailer))
        offset = record['address'] - source[0][0]
        old, new = source[0][1], updated[0][1]
        allowed = set(range(offset + 20, offset + 24)) | set(range(offset + 28, offset + 45))
        self.assertTrue({i for i, (a, b) in enumerate(zip(old, new)) if a != b} <= allowed)
        self.assertEqual(new[0x40:0x40 + len(history)], old[0x40:0x40 + len(history)])
        self.assertEqual(current(updated, 8), current(source, 8))
        with self.assertRaisesRegex(ValueError, 'changed'):
            edit(updated, dict(operation='vin', record_id=record['id'], vin=VIN,
                               expected_snapshot=record['snapshot']))
        historical = analyze(source)['vcsec'][0]
        self.assertFalse(historical['is_vin'])
        with self.assertRaises(ValueError):
            edit(source, dict(operation='vin', record_id=historical['id'], vin=NEW_VIN))

    def test_vcsec_0033_vin_rejects_corruption_ambiguity_and_unknown_layout(self):
        valid = VIN.encode() + b'\xA5\xF0\x01\x7E'
        cases = [journal({0: [entry(0x33, valid, bad_crc=True)]}),
                 journal({0: [entry(0x33, valid, revision=2)]}),
                 journal({0: [entry(0x33, valid, app_format=2)]}),
                 journal({0: [entry(0x33, valid, status=0)]}),
                 journal({0: [entry(0x33, valid)]}, active=(0, 2)),
                 journal({0: [entry(0x33, valid[:-1])]}),
                 journal({0: [entry(0x33, b'\x00' * 4 + VIN.encode())]}),
                 journal({0: [entry(0x33, b'I' + valid[1:])]}),
                 journal({0: [entry(0x33, valid.lower())]})]
        for source in cases:
            record = analyze(source)['vcsec'][0]
            self.assertFalse(record['is_vin'], record['context'])
            before = source[0][1]
            with self.assertRaises(ValueError):
                edit(source, dict(operation='vin', record_id=record['id'], vin=NEW_VIN))
            self.assertEqual(source[0][1], before)

    def test_spc56_padding_noop_and_explicit_normalization(self):
        payload = b'setting 1\r\n#' + b'\xff' * 6
        source = [(0x1C400, b'\x00\x00' + len(payload).to_bytes(2, 'big') + payload + b'\xff' * 20)]
        result = analyze(source)
        record, = result['spc56']
        self.assertEqual(record['name'], 'internal.dat')
        self.assertEqual(record['padding_byte_count'], 6)
        self.assertEqual(record['text'], 'setting 1\r\n#')
        self.assertEqual(result['tesla'], [])
        self.assertEqual(edit(source, dict(operation='spc56', record_id=record['id'], text=record['text'])), source)
        updated = edit(source, dict(operation='spc56', record_id=record['id'], text=record['text'],
                                   normalization='lineFeedTerminated', padding_policy='remove'))
        new = analyze(updated)['spc56'][0]
        self.assertEqual(new['text'], 'setting 1\n#\n')
        self.assertEqual(new['padding_byte_count'], 0)
        self.assertEqual(updated[0][1][4 + new['byte_count']:], b'\xff' * (len(source[0][1]) - 4 - new['byte_count']))

    def test_spc56_growth_shrink_bounds_and_original_neighbors(self):
        value = b'a 12345\nb 2\n'
        raw = b'\x00\x00' + len(value).to_bytes(2, 'big') + value + b'\xff' * 3 + b'\x9A'
        source = [(0x18000, raw[:7]), (0x18007, raw[7:])]
        record = analyze(source)['spc56'][0]
        updated = edit(source, dict(operation='spc56', record_id=record['id'], text='a 3\n'))
        merged = b''.join(d for _, d in updated)
        self.assertEqual(merged[:8], b'\x00\x00\x00\x04a 3\n')
        self.assertEqual(merged[8:-1], b'\xff' * (len(raw) - 9))
        self.assertEqual(merged[-1], 0x9A)
        for value in ('x' * 65536, 'x' * 17, 'не ASCII', 'a\x00'):
            with self.assertRaises(ValueError):
                edit(source, dict(operation='spc56', record_id=record['id'], text=value))
        growth = edit(source, dict(operation='spc56', record_id=record['id'], text='x' * (len(raw) - 5)))
        self.assertEqual(b''.join(d for _, d in growth)[-1], 0x9A)

    def test_spc56_invalid_payloads_gaps_headers_and_empty_text(self):
        for value in (b'A\xffB\xff', b'\xff' * 6, b'A\x00B', b'\xff\x01'):
            self.assertEqual(analyze([(0x1C400, b'\x00\x00' + len(value).to_bytes(2, 'big') + value)])['spc56'], [])
        self.assertEqual(analyze([(0x18000, b'\x00\x00\xff\xff')])['spc56'], [])
        self.assertEqual(analyze([(0x18000, b'\x00\x01\x00\x00')])['spc56'], [])
        self.assertEqual(analyze([(0x18000, b'\x00\x00\x00\x04a'), (0x18006, b' 1\n')])['spc56'], [])
        source = [(0x18000, b'\x00\x00\x00\x00')]
        record = analyze(source)['spc56'][0]
        self.assertEqual(record['text'], '')
        self.assertEqual(edit(source, dict(operation='spc56', record_id=record['id'], text='')), source)

    def test_tesla_scanning_is_suppressed_by_structures_unless_requested(self):
        source = journal({0: [entry(8, (tesla(b'X') + b'\x00' * 34)[:34])]})
        self.assertEqual(analyze(source)['tesla'], [])
        self.assertTrue(analyze(source, force_tesla=True)['tesla'])

    def test_invalid_input_requests_do_not_mutate_caller_buffers(self):
        data = bytearray(tesla(b'ABC'))
        source = [(0, data)]
        original = bytes(data)
        for request in (None, {}, {'operation': 'parameter', 'record_id': []},
                        {'operation': 'parameter', 'record_id': 'tesla-00000000', 'value_hex': '0G00'},
                        {'operation': 'parameter', 'record_id': 'tesla-00000000', 'value_hex': '444546', 'expected_snapshots': []}):
            with self.assertRaises(ValueError):
                edit(source, request)
            self.assertEqual(data, original)
        for segments in ([(0, b'12'), (1, b'34')], [(-1, b'X')], [(0xFFFFFFFF, b'XY')], [(True, b'X')], [(0, b'')]):
            with self.assertRaises(ValueError):
                analyze(segments)


if __name__ == '__main__':
    unittest.main()
