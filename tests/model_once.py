"""Independent bit-clocked single-core e200z0 OnCE model for wire-path tests.

This models only the documented subset used by this driver. It neither imports
production OnCE constants nor claims to emulate a complete e200 CPU.
"""
from tests.fake_mpsse import FakeTarget


class OnceTarget(FakeTarget):
    def __init__(self):
        self.aux = False
        self.command = 0
        self.registers = {}
        self.memory = {}
        self.gprs = [0] * 32
        self.scan = [0] * 6
        self.handoff = False
        self.executed = []
        self.fetches = []
        self.reset_asserted = False
        self.halted = False
        self.instruction_error = False
        super().__init__(0x1234501D, 5)

    def _enter(self, nxt):
        if nxt == 'RESET':
            # Test-Logic-Reset resets the selected TAP; it does not return
            # ownership to JTAGC. AN4365 1.3.2 requires a DRPAUSE handoff.
            self.command = 0x202 if self.aux else 0
            self.ir_width = 10 if self.aux else 5
            self.handoff = False
        elif nxt == 'IRCAPTURE':
            status = (0x209 if self.halted else 0x201) | (0x100 if self.instruction_error else 0)
            self.ir_shift = status if self.aux else 1
        elif nxt == 'IRUPDATE':
            if not self.aux:
                if self.ir_shift == 0x11:
                    self.aux = True
                    self.ir_width = 10
            else:
                self.command = self.ir_shift
        elif nxt == 'DRCAPTURE':
            if not self.aux:
                self.dr, self.dr_width = self.idcode, 32
            else:
                reg = self.command & 0x7F
                if reg == 0x10:
                    self.dr_width = 192
                    self.dr = sum(v << (i * 32) for i, v in enumerate(self.scan))
                elif reg == 0x11:
                    self.dr, self.dr_width = 0, 1
                elif reg == 0x02:
                    self.dr, self.dr_width = 0x07C5101D, 32
                else:
                    self.dr, self.dr_width = self.registers.get(reg, 0), 32
        elif nxt == 'DRPAUSE' and self.aux:
            self.handoff = True
        elif nxt == 'DRUPDATE' and self.aux:
            if self.handoff:
                self.aux, self.handoff, self.ir_width = False, False, 5
            else:
                reg = self.command & 0x7F
                if not self.command & 0x200:
                    if reg == 0x10:
                        self.scan = [(self.dr >> (i * 32)) & 0xFFFFFFFF for i in range(6)]
                    elif reg != 0x11:
                        self.registers[reg] = self.dr
                        if reg == 0x12 and self.dr & 1 and not self.reset_asserted:
                            self.halted = True
                if self.command & 0x100:
                    self.execute()
        self.state = nxt

    def reset_pin(self, asserted):
        self.reset_asserted = asserted
        if not asserted and self.registers.get(0x12, 0) & 1:
            self.halted = True

    def execute(self):
        wbbr, _, _, pc, op, ctl = self.scan
        self.executed.append(op)
        if not self.halted:
            raise AssertionError('CPU instruction executed outside debug')
        if not ctl & 0x400:
            raise AssertionError('operand injection not enabled')
        if not ctl & 2:
            # Model the IR format tag independently of MMU support. Supplying
            # our VLE opcodes as Book E cannot execute the requested operation.
            self.instruction_error = True
            return
        self.instruction_error = False
        # Instruction prefetch may precede the injected store completing.
        # Resolve it BEFORE effects, so a bootstrap cannot initialize the very
        # SRAM it is already fetching from. BAM is independent immutable ROM.
        next_pc = pc + 4
        self.fetches.append(next_pc)
        if 0xFFFFC000 <= next_pc <= 0xFFFFFFFC:
            fetched = 0x1800D000  # synthetic ROM instruction, never executed
        else:
            try:
                fetched = self.read_word(next_pc)
            except KeyError as exc:
                raise AssertionError('instruction fetch from uninitialized memory') from exc
        if op == 0x181F0900:
            for i, value in enumerate(self.gprs):
                self.write_word(wbbr + 4 * i, value)
        elif op == 0x501F0000:
            self.scan[0] = self.gprs[0] = self.read_word(wbbr)
        elif op in (0x1BE1D000, 0x1BE1D001):
            self.scan[0] = self.gprs[1] = wbbr | (op & 1)
        elif op == 0x543F0000:
            self.write_word(wbbr, self.gprs[1])
        else:
            raise AssertionError(f'unknown VLE instruction {op:08x}')
        self.scan[3] = next_pc
        self.scan[4] = fetched

    def read_word(self, address):
        return int.from_bytes(bytes(self.memory[address + i] for i in range(4)), 'big')

    def write_word(self, address, value):
        for i, byte in enumerate(value.to_bytes(4, 'big')):
            self.memory[address + i] = byte
