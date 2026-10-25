"""Synthetic S-record fixtures only; no customer firmware or hardware."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spc5tool.images import decode_image, ADDRESS_BYTES
from spc5tool.cli import main


def record(kind, address, data=b''):
    body = address.to_bytes(ADDRESS_BYTES[kind], 'big') + data
    body = bytes([len(body) + 1]) + body
    return ('S' + kind + (body + bytes([(~sum(body)) & 255])).hex().upper() + '\r\n').encode()


class ImageTests(unittest.TestCase):
    def test_mixed_widths_missing_optional_markers_and_addresses(self):
        raw = record('1', 0xFFF0, b'a' * 16) + record('2', 0x10000, b'b' * 16)
        image = decode_image('CFlash.S19', raw)
        self.assertEqual(image.segments, ((0xFFF0, 0, 32),))
        self.assertEqual(image.select(), (0xFFF0, b'a' * 16 + b'b' * 16))
        for base in (0x800000, 0x400000, 0xC00000):
            image = decode_image('fixture.S19', record('2', base, b'12345678'))
            self.assertEqual(image.select(), (base, b'12345678'))

    def test_sparse_segments_are_sorted_never_filled_or_relocated(self):
        image = decode_image('firmware.srec', record('3', 0x800000, b'BBBBBBBB') + record('1', 0, b'AAAAAAAA'))
        self.assertEqual(image.data, b'AAAAAAAABBBBBBBB')
        self.assertEqual(image.segments, ((0, 0, 8), (0x800000, 8, 8)))
        with self.assertRaisesRegex(ValueError, 'multiple segments'):
            image.select()
        self.assertEqual(image.select(1), (0x800000, b'BBBBBBBB'))
        with self.assertRaisesRegex(ValueError, 'relocation'):
            image.select(1, 0)
        for index in (-1, 2, True):
            with self.assertRaises(ValueError):
                image.select(index)

    def test_markers_counts_termination_and_checksum(self):
        raw = record('0', 0, b'TEST') + record('3', 0x10000000, b'abcd') + record('5', 1) + record('7', 0x10000000)
        self.assertEqual(decode_image('fixture.s37', raw).entry, 0x10000000)
        bad = [raw[:-4] + b'00\r\n', raw + record('1', 0, b'a'),
               record('3', 0, b'abc') + record('5', 2),
               record('1', 0, b'abcd') + record('2', 3, b'x'),
               record('3', 0xFFFFFFFF, b'ab'), b'S40100FE\n', b'not srecords', b'\xff',
               b'S1030000ZZ\n', b'S1040000FC\n', record('0', 0, b'HEADER ONLY')]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                decode_image('bad.s19', data)

    def test_binary_and_sniffed_srecords(self):
        binary = decode_image('firmware.bin', b'\x00\xff\x01\x80')
        self.assertEqual(binary.select(address=0x100), (0x100, b'\x00\xff\x01\x80'))
        with self.assertRaisesRegex(ValueError, 'destination address'):
            binary.select()
        self.assertTrue(decode_image('renamed.bin', record('1', 0, b'abcd')).segments)
        with patch('spc5tool.images.MAX_IMAGE', 2), self.assertRaisesRegex(ValueError, '16 MiB'):
            decode_image('large.s19', record('1', 0, b'abcd'))

    def test_cli_srecord_plan_and_wrong_address_fail_before_usb(self):
        with tempfile.TemporaryDirectory() as directory, patch('spc5tool.cli.backend', side_effect=AssertionError('USB opened')):
            path = Path(directory) / 'fixture.S19'
            path.write_bytes(record('2', 0x800000, b'12345678'))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(['write', 'spc560b64', str(path)]), 0)
            plan = json.loads(out.getvalue())
            self.assertEqual(plan['address'], '0x00800000')
            self.assertEqual(plan['bytes'], 8)
            self.assertFalse(plan['executed'])
            with contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(main(['write', 'spc560b64', '--address', '0', str(path)]), 2)
            self.assertIn('relocation', error.getvalue())
