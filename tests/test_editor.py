"""Immutable image edits, physical S-record addresses and edit-to-Write flow."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spc5tool.editor import DumpDocument
from spc5tool.images import decode_image
from spc5tool.srecordizer import analyze
from tests.test_images import record
from tests.test_srecordizer import tesla, VIN, NEW_VIN, journal, entry, keyslot, SECOND
from tests import test_web


class EditorDocumentTests(unittest.TestCase):
    def test_srec_edit_preserves_order_addresses_whitespace_and_unmodified_lines(self):
        raw = (record('0', 0, b'SYNTHETIC') + b'\r\n' + record('3', 0xC00000, b'untouched')
               + b'  ' + record('2', 0x800000, b'ABCD').rstrip() + b'  \n'
               + record('2', 0x800004, b'EFGH') + record('5', 3) + record('7', 0x800000))
        doc = DumpDocument('sparse.s19', raw)
        changed = doc.apply(dict(operation='bytes', address=0x800002, value_hex='31323334'))
        encoded = doc.serialize(changed)
        self.assertEqual(decode_image('sparse.s19', encoded).data, b'AB1234GHuntouched')
        self.assertEqual(encoded.splitlines()[0:3], raw.splitlines()[0:3])
        self.assertEqual(encoded.splitlines()[-2:], raw.splitlines()[-2:])
        self.assertIn(b'  \n', encoded)
        self.assertEqual(doc.source, raw)
        self.assertEqual(doc.serialize(doc.segments), raw)
        self.assertEqual(decode_image('sparse.s19', encoded).segments, doc.image.segments)

    def test_binary_mapping_export_and_rejection_of_resize_or_gap(self):
        doc = DumpDocument('data.bin', b'ABCDEFGH', 0x800000)
        edited = doc.apply(dict(operation='bytes', address=0x800004, value_hex='01020304'))
        self.assertEqual(doc.serialize(edited), b'ABCD\x01\x02\x03\x04')
        exported = decode_image('data.s19', doc.export_srec())
        self.assertEqual(exported.select(), (0x800000, b'ABCDEFGH'))
        for address in (0, 0x7FFFFF, 0x800005):
            with self.assertRaises(ValueError):
                doc.apply(dict(operation='bytes', address=address, value_hex='01020304'))
        with self.assertRaises(ValueError):
            doc.serialize([(0x800000, b'longer than source')])
        with self.assertRaises(ValueError):
            DumpDocument('overflow.bin', b'ab', 0xFFFFFFFF)
        sparse = DumpDocument('sparse.mhx', record('2', 0x100, b'AB') + record('2', 0x104, b'CD'))
        with self.assertRaises(ValueError):
            sparse.apply(dict(operation='bytes', address=0x101, value_hex='010203'))
        self.assertEqual(sparse.search('4243')['addresses'], [])

    def test_replace_all_is_equal_length_and_segment_bounded(self):
        doc = DumpDocument('sample.bin', b'ABCABC', 0x1000)
        self.assertEqual(doc.search('414243')['addresses'], [0x1000, 0x1003])
        self.assertEqual(doc.serialize(doc.apply(dict(operation='replace', find_hex='414243', value_hex='010203'))), b'\x01\x02\x03' * 2)
        for change in (dict(operation='replace', find_hex='414243', value_hex='01'),
                       dict(operation='replace', find_hex='FF', value_hex='00'),
                       dict(operation='bytes', address=0x1000, value_hex='GG'),
                       dict(operation='bytes', address=0x1000, value_hex='01', expected_hex='FF')):
            with self.assertRaises(ValueError):
                doc.apply(change)


class EditorHTTPTests(unittest.TestCase):
    # Reuse only the HTTP fixture helpers, not the entire WebTests test suite.
    setUp = test_web.WebTests.setUp
    tearDown = test_web.WebTests.tearDown
    call = test_web.WebTests.call
    complete = test_web.WebTests.complete
    payload = test_web.WebTests.payload

    def upload(self, data=None, name='editor.bin', target='spc560b64', base=0):
        data = data if data is not None else tesla(VIN.encode()) + b'\xff' * (0x8000 - 21)
        code, image = self.call('/api/files?name=' + name, data, raw=True)
        self.assertEqual(code, 200, image)
        return image, dict(target=target, file_id=image['id'], sha256=image['sha256'], base=base)

    def test_editor_analyze_change_undo_source_and_live_write_uses_new_image(self):
        original, request = self.upload()
        _, parsed = self.call('/api/editor/analyze', request)
        row = parsed['tesla'][0]
        oldplan = self.call('/api/plan', self.payload(action='write', file_id=original['id']))[1]
        code, result = self.call('/api/editor/apply', {**request, 'change':
            dict(operation='vin', record_id=row['id'], vin=NEW_VIN, expected_snapshot=row['snapshot'])})
        self.assertEqual(code, 200, result)
        edited = result['file']
        self.assertNotEqual(edited['id'], original['id'])
        self.assertNotEqual(edited['sha256'], original['sha256'])
        self.assertEqual(self.call('/api/run', {'plan_id': oldplan['id']})[0], 400)
        self.assertEqual(analyze([(0, self.call('/api/download/' + original['id'])[1])])['tesla'][0]['ascii'], VIN)
        changed = self.call('/api/download/' + edited['id'])[1]
        self.assertEqual(analyze([(0, changed)])['tesla'][0]['ascii'], NEW_VIN)
        job = self.complete(self.payload(action='write', file_id=edited['id'], speed_mode=True))
        self.assertEqual(job['status'], 'complete', job)
        read = self.complete(self.payload(action='read', length=0x8000))
        self.assertEqual(self.call('/api/download/' + read['result']['file']['id'])[1], changed)
        self.usb_open.assert_not_called()

    def test_bad_hash_source_tampering_target_and_address_do_not_edit_or_open_usb(self):
        image, request = self.upload()
        for payload in ({**request, 'sha256': '0' * 64}, {**request, 'target': 'spc5748g'}):
            self.assertEqual(self.call('/api/editor/analyze', payload)[0], 400)
        result = self.call('/api/editor/apply', {**request, 'change':dict(operation='bytes',address=0,value_hex='A5')})[1]
        edited = result['file']
        self.assertEqual(self.call('/api/plan', self.payload('spc5668g','write',file_id=edited['id']))[0], 400)
        self.assertEqual(self.call('/api/plan', self.payload(action='write',address=8,file_id=edited['id']))[0], 400)
        _, path = self.server.workspace.file(image['id'])
        path.write_bytes(b'\xff' * image['size'])
        self.assertEqual(self.call('/api/editor/analyze', request)[0], 400)
        self.usb_open.assert_not_called()

    def test_srec_crc_roundtrip_sparse_segments_and_export_selected_bin(self):
        first = tesla(VIN.encode())
        raw = record('2', 0x18000, first) + record('2', 0x1C400, b'UNCHANGED')
        image, request = self.upload(raw, 'sparse.mhx', target='spc5668g')
        row = self.call('/api/editor/analyze', request)[1]['tesla'][0]
        result = self.call('/api/editor/apply', {**request,'change':dict(operation='vin',record_id=row['id'],vin=NEW_VIN)})[1]
        edited = result['file']; output = self.call('/api/download/' + edited['id'])[1]
        parsed = decode_image('edited.s19', output)
        self.assertEqual(parsed.segments, decode_image('original.s19', raw).segments)
        self.assertEqual(output.splitlines()[-1], raw.splitlines()[-1])
        export_request = {**request,'file_id':edited['id'],'sha256':edited['sha256'],'format':'bin','segment':1}
        code, exported = self.call('/api/editor/export', export_request)
        self.assertEqual(code, 200, exported)
        self.assertEqual(exported['file']['editor_base'], 0x1C400)
        self.assertEqual(self.call('/api/download/' + exported['file']['id'])[1], b'UNCHANGED')
        # No rebase of an edited BIN from a selected physical segment.
        self.assertEqual(self.call('/api/plan', self.payload('spc5668g','write',address=0,file_id=exported['file']['id']))[0], 400)

    def test_current_vcsec_edit_and_atomic_key_slot_update_flow(self):
        source = journal({0:[entry(5,keyslot() + b'\x00' * 1330)]})[0][1]
        _, request = self.upload(source, base=0x800000)
        parsed = self.call('/api/editor/analyze', request)[1]
        row = next(r for r in parsed['vcsec'] if r['parameter_id'] == 5)
        code, result = self.call('/api/editor/apply', {**request,'change':
            dict(operation='key',record_id=row['id'],slot=1,key_hex=SECOND.hex())})
        self.assertEqual(code, 200, result)
        data = self.call('/api/download/' + result['file']['id'])[1]
        keys = next(r for r in analyze([(0x800000,data)])['vcsec'] if r['parameter_id'] == 5)['key_slots']
        self.assertEqual(keys[1]['key_hex'], SECOND.hex().upper())
        self.assertEqual(data[0x40:0x54], source[0x40:0x54])

    def test_catalog_persists_search_raw_bytes_and_running_job_guards(self):
        image, request = self.upload(b'ABCDABCD')
        self.assertEqual(self.call('/api/editor/search', {**request,'find_hex':'41424344'})[1]['addresses'], [0,4])
        self.assertEqual(self.call('/api/editor/read', {**request,'address':0,'length':4})[1]['value_hex'], '41424344')
        self.assertEqual(self.call('/api/editor/catalog', {'catalog':[dict(id=1,name='Synthetic') ]})[0], 200)
        self.assertTrue((Path(self.temp.name) / 'srecordizer-catalog.json').exists())
        self.assertEqual(self.call('/api/editor/catalog', {'catalog':[dict(id=True,name='Invalid') ]})[0], 400)
        self.server.workspace.active = 'test-active'
        try:
            self.assertEqual(self.call('/api/editor/apply', {**request,'change':dict(operation='bytes',address=0,value_hex='FF')})[0], 400)
        finally:
            self.server.workspace.active = None
        self.assertEqual(self.call('/api/download/' + image['id'])[1], b'ABCDABCD')
