"""Offline device models, not proof of electrical or silicon compatibility.

The MCU model implements the flash register interface, including keys, locks,
selectors, interlocks, full ECC units and high-voltage completion. It does not
pretend to emulate a CPU. OnCE/MPSSE are tested separately with wire vectors.
"""
from spc5tool.errors import DriverError
from spc5tool.memory import WordMemory


class SimMemory(WordMemory):
    def __init__(self, chip):
        self.chip = chip
        # Result metadata only; Nexus queue timing is modeled in test_fast_read.
        self.fast_read_fallback = False
        self.regions = {r.base: bytearray(b"\xff" * r.size) for r in chip.regions}
        self.c55 = chip.algorithm.startswith("c55")
        bases = (chip.flash_ctrl["FMC"],) if self.c55 else (0xC3F88000, 0xC3F8C000)
        self.controllers = {b: {"mcr": 0x600, "pending": {}, "interlock": None,
                                "regs": {}, "keys": set()} for b in bases}
        self.log = []
        self.fault = None
        self.lock_stuck = False
        self.read_error = False
        # Independent map from the manuals: (address, size, ctrl, select offset, bit).
        self.blocks = []
        if chip.read_only:
            pass  # No simulated erase/program geometry for the read-only window.
        elif chip.algorithm == "c55-spc58h":
            starts = (0xFC0000, 0xFC4000, 0xFC8000, 0xFCC000, 0xFD0000, 0xFD8000, 0xFE0000, 0xFF0000, 0x1000000)
            masks = (19, 17, 20, 18, 22, 23, 24, 25)
            self.blocks += [(a, b-a, 0xF7FE0000, 0x38, bit) for a,b,bit in zip(starts, starts[1:], masks)]
            self.blocks += [(0x1000000 + i * 0x20000, 0x20000, 0xF7FE0000, 0x40, i) for i in range(2)]
            self.blocks += [(0x1040000 + i * 0x40000, 0x40000, 0xF7FE0000, 0x40 if i < 30 else 0x44, (i + 2) % 32) for i in range(38)]
            self.blocks += [(0x800000 + i * 0x10000, 0x10000, 0xF7FE0000, 0x3C, i) for i in range(4)]
        elif self.c55:
            entries = [(0xF90000 + n * 0x4000, 0x4000, n) for n in range(8)]
            entries += [(0xFB0000, 0x8000, 8), (0xFB8000, 0x8000, 9)]
            entries += [(0xFC0000 + n * 0x8000, 0x8000, 18 + n) for n in range(4)]
            entries += [(0xFE0000, 0x10000, 22), (0xFF0000, 0x10000, 24)]
            self.blocks += [(a, s, 0xFFFE0000, 0x38, bit) for a, s, bit in entries]
            self.blocks += [(0x1000000 + n * 0x40000, 0x40000, 0xFFFE0000, 0x40, n) for n in range(22)]
        else:
            starts = (0, 0x8000, 0xC000, 0x10000, 0x18000, 0x20000, 0x40000, 0x60000, 0x80000)
            self.blocks += [(a, b - a, 0xC3F88000, 0x10, (n if n < 6 else n + 10))
                            for n, (a, b) in enumerate(zip(starts, starts[1:]))]
            self.blocks += [(0x80000 + n * 0x20000, 0x20000, 0xC3F88000, 0x14, n) for n in range(8)]
            self.blocks += [(0x800000 + n * 0x4000, 0x4000, 0xC3F8C000, 0x10, n) for n in range(4)]
            self.blocks += [(0x200000, 0x4000, 0xC3F88000, 0x10, 20),
                            (0x400000, 0x2000, 0xC3F88000, 0x10, 20),
                            (0xC00000, 0x2000, 0xC3F8C000, 0x10, 20)]
            self.regions[0x200000][0x3DD8:0x3DE8] = bytes.fromhex('FEEDFACECAFEBEEF55AA55AA55AA55AA')
        if self.c55:
            self.blocks += [(0x400000, 0x4000, chip.flash_ctrl['FMC'], 0x38, 31)]
        for base, c in self.controllers.items():
            locks = (0x10, 0x14, 0x18, 0x1C) if self.c55 else ((4, 12) if base == 0xC3F8C000 else (4, 8, 12))
            selectors = (0x38, 0x3C, 0x40, 0x44) if self.c55 else ((0x10,) if base == 0xC3F8C000 else (0x10, 0x14))
            c["regs"].update({o: 0xFFFFFFFF for o in locks})
            c["regs"].update({o: 0 for o in selectors})

    def _region(self, address, size):
        for base, data in self.regions.items():
            if base <= address and address + size <= base + len(data):
                return data, address - base
        raise DriverError(f"simulator unmapped memory 0x{address:08x}")

    def read(self, address, size):
        data, offset = self._region(address, size)
        if self.read_error:
            for c in self.controllers.values():
                c["mcr"] |= 0x8000
        return bytes(data[offset:offset + size])

    def read32(self, address):
        self.word_address(address)
        for base, c in self.controllers.items():
            if address == base:
                return c["mcr"]
            if address - base in c["regs"]:
                return c["regs"][address - base]
        return int.from_bytes(self.read(address, 4), "big")

    def write32(self, address, value):
        self.word_address(address)
        self.log.append((address, value))
        for base, c in self.controllers.items():
            offset = address - base
            if offset == 0:
                old = c["mcr"]
                # W1C error flags, read-only DONE/PEG, low control fields.
                c["mcr"] = (old & (0xC600 & ~value)) | (value & 0x1F)
                if not value & 0x1F:
                    c["pending"] = {}
                    c["interlock"] = None
                if value & 1 and not old & 1:
                    c["mcr"] &= ~0x600
                    self._execute(base, c)
                elif old & 1 and not value & 1:
                    c["mcr"] |= 0x400  # abort completes
                return
            if offset in c["regs"]:
                if not self.c55 and offset in (4, 8, 12):
                    keys = {4: 0xA1A11111, 8: 0xB2B22222, 12: 0xC3C33333}
                    if value == keys[offset]:
                        c["keys"].add(offset)
                        return
                    if offset not in c["keys"] or self.lock_stuck:
                        return
                elif self.c55 and offset in (0x10, 0x14, 0x18, 0x1C) and self.lock_stuck:
                    return
                c["regs"][offset] = value
                return
        block = next((b for b in self.blocks if b[0] <= address < b[0] + b[1]), None)
        if block is None:
            raise DriverError("simulator write outside supported flash/registers")
        c = self.controllers[block[2]]
        if not c["mcr"] & 0x14 or c["mcr"] & 1:
            raise DriverError("flash data write outside interlock phase")
        if c["interlock"] is None:
            c["interlock"] = address
        c["pending"][address] = value

    def _unlocked(self, block):
        _, _, base, selector, bit = block
        regs = self.controllers[base]["regs"]
        if self.c55:
            locks = (0x10 + selector - 0x38,)
        else:
            locks = (4, 12) if selector == 0x10 else (8,)
        return all(not regs[o] & (1 << bit) for o in locks)

    def _execute(self, base, c):
        if self.fault == "timeout":
            return
        good = self.fault != "peg" and c["interlock"] is not None
        if good and c["mcr"] & 4:
            for b in self.blocks:
                a, size, ctrl, offset, bit = b
                if ctrl == base and c["regs"][offset] & (1 << bit) and self._unlocked(b):
                    if a in (0x400000, 0xC00000):
                        continue  # OTP physically ignores erase.
                    if not self.c55 and a == 0x200000 and not 0x200000 <= c['interlock'] < 0x204000:
                        continue
                    data, start = self._region(a, size)
                    data[start:start + size] = b"\xff" * size
        elif good and c["mcr"] & 0x10:
            address = c["interlock"]
            block = next(b for b in self.blocks if b[0] <= address < b[0] + b[1])
            unit = 16 if self.chip.algorithm == 'c55-spc58h' else 8
            good = address % unit == 0 and set(c["pending"]) == set(range(address, address + unit, 4)) and self._unlocked(block)
            if good:
                data, start = self._region(address, unit)
                payload = b"".join(c["pending"][address + n].to_bytes(4, "big") for n in range(0, unit, 4))
                good = data[start:start + unit] == b"\xff" * unit
                if good:
                    data[start:start + unit] = payload
                    if self.fault == "corrupt":
                        data[start] ^= 1
        c["mcr"] |= 0x400 | (0x200 if good else 0)


class SimSpiPort:
    def __init__(self, part):
        self.part = part
        self.data = bytearray(b"\xff" * part.size)
        self.status = [0, 0, 0]
        self.log = []
        self.stuck_busy = False
        self.reject_wren = False
        self.corrupt = False

    def exchange(self, command, readlen=0):
        self.log.append((bytes(command), readlen))
        op = command[0]
        if op == 0x9F:
            return self.part.jedec
        if op in (5, 0x35, 0x15):
            index = {5: 0, 0x35: 1, 0x15: 2}[op]
            return bytes([self.status[index] | (int(self.stuck_busy) if index == 0 else 0)])
        if op == 6:
            if not self.reject_wren:
                self.status[0] |= 2
            return b""
        address = int.from_bytes(command[1:4], "big")
        if op == 3:
            return bytes(self.data[address:address + readlen])
        if not self.status[0] & 2:
            raise DriverError("simulated SPI WEL is clear")
        self.status[0] &= ~2
        if op == 0x20:
            if address % 4096:
                raise DriverError("unaligned SPI erase")
            self.data[address:address + 4096] = b"\xff" * 4096
        elif op == 2:
            payload = command[4:]
            if len(payload) > 256 - address % 256:
                raise DriverError("SPI page wrapped")
            for i, value in enumerate(payload):
                self.data[address + i] &= value
            if self.corrupt:
                self.data[address] ^= 1
        else:
            raise DriverError(f"unimplemented simulated SPI command {op:02x}")
        return b""
