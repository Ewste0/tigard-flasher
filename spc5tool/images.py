"""Firmware input decoding. S-record addresses are never relocated or gap-filled."""
from dataclasses import dataclass
from pathlib import Path

MAX_IMAGE = 16 * 1024 * 1024
MAX_UPLOAD = 64 * 1024 * 1024
SREC_SUFFIXES = {'.s19', '.s28', '.s37', '.srec', '.srecord', '.mot'}
ADDRESS_BYTES = {'0': 2, '1': 2, '2': 3, '3': 4, '5': 2, '6': 3, '7': 4, '8': 3, '9': 2}


@dataclass(frozen=True)
class FirmwareImage:
    data: bytes
    segments: tuple = ()  # (physical address, offset in data, size)
    entry: int | None = None

    def select(self, index=None, address=None):
        if not self.segments:
            if index is not None:
                raise ValueError('segment selection applies only to S-record images')
            if address is None:
                raise ValueError('a raw binary requires a destination address')
            return address, self.data
        if index is None:
            if len(self.segments) != 1:
                raise ValueError('S-record contains multiple segments; select one explicitly')
            index = 0
        if type(index) is not int or not 0 <= index < len(self.segments):
            raise ValueError('invalid S-record segment')
        base, offset, size = self.segments[index]
        if address is not None and address != base:
            raise ValueError(f'S-record segment requires address 0x{base:08X}; relocation is not supported')
        return base, self.data[offset:offset + size]


def decode_image(name, raw):
    if not raw or len(raw) > MAX_UPLOAD:
        raise ValueError('input must contain 1 byte to 64 MiB')
    is_srec = Path(name).suffix.lower() in SREC_SUFFIXES
    # Catch recognizable text renamed to .bin instead of programming ASCII.
    if not is_srec:
        first = raw.lstrip()[:2]
        is_srec = first in (b'S0', b'S1', b'S2', b'S3') and all(c in b'0123456789ABCDEFabcdef\r\n\t S' for c in raw[:512])
    if not is_srec:
        if len(raw) > MAX_IMAGE:
            raise ValueError('decoded image exceeds 16 MiB')
        return FirmwareImage(bytes(raw))
    try:
        lines = raw.decode('ascii').splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError('S-record input must be ASCII text') from exc
    records = []
    total = 0
    entry = None
    ended = False
    count_seen = False
    for line_number, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        def fail(message):
            raise ValueError(f'S-record line {line_number}: {message}')
        if ended:
            fail('record after termination')
        if len(line) < 4 or len(line) > 514 or line[0] != 'S' or line[1] not in ADDRESS_BYTES:
            fail('invalid record type or length')
        if len(line) % 2 or any(c not in '0123456789ABCDEFabcdef' for c in line[2:]):
            fail('invalid hexadecimal data')
        record = bytes.fromhex(line[2:])
        address_bytes = ADDRESS_BYTES[line[1]]
        if len(record) != record[0] + 1 or record[0] < address_bytes + 1:
            fail('byte count does not match record')
        if sum(record) & 0xFF != 0xFF:
            fail('checksum mismatch')
        address = int.from_bytes(record[1:address_bytes + 1], 'big')
        data = record[address_bytes + 1:-1]
        kind = line[1]
        if kind in '123':
            if count_seen:
                fail('data after record-count marker')
            if not data or address + len(data) > 0x100000000:
                fail('empty data or address overflow')
            records.append((address, data))
            total += len(data)
            if total > MAX_IMAGE:
                fail('decoded image exceeds 16 MiB')
        elif kind in '56':
            if data or count_seen or address != len(records):
                fail('invalid data-record count')
            count_seen = True
        elif kind in '789':
            if data:
                fail('termination record cannot contain data')
            entry, ended = address, True
        elif records or address != 0:
            fail('invalid header position or address')
    if not records:
        raise ValueError('S-record has no data records')
    # Some programmers omit S0/count/termination, and mix S1/S2/S3 widths.
    # Those are valid inputs; every provided record is still checked.
    output = bytearray()
    segments = []
    for address, data in sorted(records):
        if segments and address < segments[-1][0] + segments[-1][2]:
            raise ValueError(f'S-record contains overlapping data at 0x{address:08X}')
        if segments and address == segments[-1][0] + segments[-1][2]:
            base, offset, size = segments[-1]
            segments[-1] = (base, offset, size + len(data))
        else:
            segments.append((address, len(output), len(data)))
            if len(segments) > 1024:
                raise ValueError('S-record has more than 1024 separate segments')
        output.extend(data)
    return FirmwareImage(bytes(output), tuple(segments), entry)
