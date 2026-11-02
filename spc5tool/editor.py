"""Immutable dump editing with physical addresses and lossless S-record output.

Editing never opens USB. An edited file is a new reviewed image, not an
in-place change to a queued hardware job or to the uploaded original.
"""
from dataclasses import dataclass

from .images import decode_image, ADDRESS_BYTES

EDITOR_TARGETS = frozenset(('spc560b64', 'spc5668g'))


def integer(value, name='address'):
    if isinstance(value, str):
        value = int(value, 0)
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f'{name} must be a 32-bit unsigned integer')
    return value


def hex_bytes(value):
    if not isinstance(value, str):
        raise ValueError('enter hexadecimal bytes')
    value = ''.join(value.split())
    if len(value) % 2 or any(c not in '0123456789abcdefABCDEF' for c in value):
        raise ValueError('HEX requires complete byte pairs')
    if not value:
        raise ValueError('enter at least one byte')
    return bytes.fromhex(value)


def read_segments(segments, address, size):
    integer(address)
    if type(size) is not int or size <= 0 or address + size > 0x100000000:
        raise ValueError('invalid byte range')
    for base, data in segments:
        if base <= address and address + size <= base + len(data):
            return data[address - base:address - base + size]
    raise ValueError('edit/read crosses a gap or is outside the loaded image')


def record(kind, address, data=b''):
    body = address.to_bytes(ADDRESS_BYTES[kind], 'big') + data
    body = bytes([len(body) + 1]) + body
    return ('S' + kind + (body + bytes([(~sum(body)) & 255])).hex().upper()).encode('ascii')


@dataclass
class DumpDocument:
    name: str
    source: bytes
    raw_base: int = 0

    def __post_init__(self):
        integer(self.raw_base, 'binary base address')
        self.image = decode_image(self.name, self.source)
        self.segments = ([(a, self.image.data[o:o + n]) for a, o, n in self.image.segments]
                         if self.image.segments else [(self.raw_base, self.image.data)])
        if any(a + len(d) > 0x100000000 for a, d in self.segments):
            raise ValueError('image exceeds the 32-bit address space')

    def info(self):
        return {'format': 'srec' if self.image.segments else 'bin',
                'entry_address': self.image.entry, 'size': len(self.image.data),
                'segments': [{'address': a, 'size': len(d)} for a, d in self.segments]}

    def changed(self, segments):
        if [(a, len(d)) for a, d in segments] != [(a, len(d)) for a, d in self.segments]:
            raise ValueError('editing cannot resize, relocate or fill gaps in the image')
        changes = []
        for (base, old), (_, new) in zip(self.segments, segments):
            if new != old:
                changes.append({'address': base, 'size': len(old),
                                'changed_bytes': sum(a != b for a, b in zip(old, new))})
        return changes

    def apply(self, request, *, catalog=None):
        operation = request.get('operation')
        segments = list(self.segments)
        if operation == 'bytes':
            address = integer(request.get('address'))
            value = hex_bytes(request.get('value_hex'))
            previous = read_segments(segments, address, len(value))
            if request.get('expected_hex') is not None and hex_bytes(request['expected_hex']) != previous:
                raise ValueError('selected bytes changed; reload the editor')
            for i, (base, data) in enumerate(segments):
                if base <= address and address + len(value) <= base + len(data):
                    offset = address - base
                    segments[i] = (base, data[:offset] + value + data[offset + len(value):])
                    break
        elif operation == 'replace':
            needle, value = hex_bytes(request.get('find_hex')), hex_bytes(request.get('value_hex'))
            if len(needle) != len(value):
                raise ValueError('replacement must have the same byte length')
            if not any(needle in data for _, data in segments):
                raise ValueError('no matching bytes in this image')
            segments = [(a, data.replace(needle, value)) for a, data in segments]
        else:
            from .srecordizer import edit
            segments = edit(segments, request, catalog=catalog)
        self.changed(segments)  # Validate the complete result before encoding.
        return segments

    def serialize(self, segments):
        self.changed(segments)
        if not self.image.segments:
            return bytes(segments[0][1])
        output = []
        # Retain record types, addresses, ordering, comments in S0, optional
        # markers and original whitespace/line endings. Rebuild only changed
        # data records and their checksum; sparse gaps never become FF bytes.
        for original in self.source.splitlines(keepends=True):
            stripped = original.strip()
            if not stripped or stripped[1:2] not in (b'1', b'2', b'3'):
                output.append(original)
                continue
            kind = chr(stripped[1])
            raw = bytes.fromhex(stripped[2:].decode('ascii'))
            width = ADDRESS_BYTES[kind]
            address = int.from_bytes(raw[1:1 + width], 'big')
            old = raw[1 + width:-1]
            new = read_segments(segments, address, len(old))
            if old == new:
                output.append(original)
            else:
                prefix = len(original) - len(original.lstrip())
                suffix = original[prefix + len(stripped):]
                output.append(original[:prefix] + record(kind, address, new) + suffix)
        result = b''.join(output)
        checked = decode_image(self.name, result)
        if checked.data != b''.join(data for _, data in segments) or checked.segments != self.image.segments:
            raise ValueError('edited S-record did not retain its original address map')
        return result

    def export_srec(self):
        if self.image.segments:
            return self.source
        lines, count = [record('0', 0, b'Tigard Flasher')], 0
        for base, data in self.segments:
            for offset in range(0, len(data), 32):
                lines.append(record('3', base + offset, data[offset:offset + 32]))
                count += 1
        lines.append(record('5' if count <= 65535 else '6', count))
        if self.image.entry is not None:
            lines.append(record('7', self.image.entry))
        return b'\r\n'.join(lines) + b'\r\n'

    def search(self, value, *, limit=500):
        needle = hex_bytes(value)
        matches = []
        for base, data in self.segments:
            cursor = 0
            while (offset := data.find(needle, cursor)) >= 0:
                if len(matches) == limit:
                    return {'addresses': matches, 'truncated': True}
                matches.append(base + offset)
                cursor = offset + len(needle)
        return {'addresses': matches, 'truncated': False}
