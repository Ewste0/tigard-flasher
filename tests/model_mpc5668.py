"""Bit-clocked MPC5668 JTAGC/OnCE/Nexus subset from RM34/35, no real USB.

The flash peripheral is modeled separately from the transport and has its own
manual-derived geometry. This is protocol testing, not silicon qualification.
"""
from tests.fake_mpsse import FakeTarget
from tests.simulator import SimMemory
from spc5tool.chips import SPC5668G


class Mpc5668Target(FakeTarget):
    def __init__(self, idcode=0x1826801D, secondary_reset=False):
        super().__init__(idcode, 5)
        self.core = None
        self.cores = {0x11: {}, 0x19: {}}
        self.halted = set()
        self.secondary_reset = secondary_reset
        self.reset_asserted = False
        self.handoff = False
        self.command = 0
        self.address_phase = True
        self.nexus_register = self.rwa = self.rwd = self.rwcs = 0
        self.nexus_write = False
        self.bus_error = None
        self.trace = []
        self.flash = SimMemory(SPC5668G)
        self.peripherals = {0xFFF38000: 0xFF00000B,
                            0xFFFF801C: 0x0800FF51, 0xFFFF8020: 0x3000FF51}

    def reset_pin(self, asserted):
        self.trace.append(('reset', asserted))
        self.reset_asserted = asserted
        if not asserted:
            for core, regs in self.cores.items():
                if regs.get(0x12, 0) & 1 and not (core == 0x19 and self.secondary_reset):
                    self.halted.add(core)

    def _enter(self, nxt):
        if nxt == 'RESET':
            self.command = 0x202 if self.core is not None else 0
            self.ir_width = 10 if self.core is not None else 5
            self.handoff = False
        elif nxt == 'IRCAPTURE':
            self.ir_shift = (0x209 if self.core in self.halted else
                             0x41 if self.core == 0x19 and self.secondary_reset else 0x201) if self.core else 1
        elif nxt == 'IRUPDATE':
            self.trace.append(('ir', self.ir_width, self.ir_shift))
            if self.core is None:
                if self.ir_shift in (0x11, 0x19):
                    self.core, self.ir_width = self.ir_shift, 10
            else:
                self.command = self.ir_shift
                self.address_phase = True
                if self.command & 0x100:
                    raise AssertionError('MPC5668 Nexus path must not execute an injected CPU instruction')
        elif nxt == 'DRCAPTURE':
            if self.core is None:
                self.dr, self.dr_width = self.idcode, 32
            elif self.command == 0x7C:
                if self.core != 0x11 or self.core not in self.halted:
                    raise AssertionError('physical Nexus bus access needs halted primary e200z6')
                self.dr_width = 8 if self.address_phase else 32
                self.dr = 0
                if not self.address_phase and not self.nexus_write:
                    self.dr = {7: self.rwcs, 10: self.rwd}[self.nexus_register]
            else:
                reg = self.command & 0x7F
                self.dr_width = 1 if reg == 0x11 else 32
                self.dr = self.cores[self.core].get(reg, 0)
        elif nxt == 'DRPAUSE' and self.core is not None:
            self.handoff = True
        elif nxt == 'DRUPDATE' and self.core is not None:
            if self.handoff:
                self.core, self.handoff, self.ir_width = None, False, 5
            elif self.command == 0x7C:
                if self.address_phase:
                    self.nexus_register, self.nexus_write = self.dr >> 1, bool(self.dr & 1)
                elif self.nexus_write:
                    self._nexus_put(self.nexus_register, self.dr)
                self.address_phase = not self.address_phase
            elif not self.command & 0x200:
                reg = self.command & 0x7F
                if reg != 0x11:
                    self.trace.append(('core', self.core, reg, self.dr))
                    self.cores[self.core][reg] = self.dr
        self.state = nxt

    def _nexus_put(self, register, value):
        self.trace.append(('nexus', register, value))
        if register == 9:
            self.rwa = value
        elif register == 7:
            if value not in (0x90000004, 0xD0000004):
                raise AssertionError('unexpected Nexus map, size, count or priority')
            self.rwcs = value & 0x7FFFFFFF
            if not value & 0x40000000:
                self._bus(False)
        elif register == 10:
            if self.rwcs != 0x50000004:
                raise AssertionError('write data must follow armed RWCS write access')
            self.rwd = value
            self._bus(True)
        else:
            raise AssertionError('unexpected Nexus register write')

    def _bus(self, writing):
        self.trace.append(('bus', 'write' if writing else 'read', self.rwa))
        if self.rwa == self.bus_error:
            self.rwcs = 0x10000006
            return
        if writing:
            value = int.from_bytes(self.rwd.to_bytes(4, 'little'), 'big')
            if self.rwa in self.peripherals or self.rwa == 0xFFF38010:
                self.peripherals[self.rwa] = value
            else:
                self.flash.write32(self.rwa, value)
            self.rwcs = 0x50000004
        else:
            value = self.peripherals.get(self.rwa)
            if value is None:
                value = self.flash.read32(self.rwa)
            self.rwd = int.from_bytes(value.to_bytes(4, 'big'), 'little')
            self.rwcs = 0x10000005
