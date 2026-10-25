"""OnCE register protocol from NXP AN4365 sections 2 and 3.

Power Architecture drawings often number the MSB as zero. OCR DR is integer
bit 0, WKUP bit 2; OSR DEBUG is bit 3, ERR bit 8. All scans are LSB first.
"""
import time
import math
from dataclasses import dataclass

from .errors import DriverError, OperationTimeout
from .security import validate_login

READ, GO = 0x200, 0x100
CPUSCR, NOREG, OCR, DBSR, DBCR0, NEXUS = 0x10, 0x11, 0x12, 0x30, 0x31, 0x7C


@dataclass
class CpuScan:
    wbbr: int = 0
    wbbr_high: int = 0
    msr: int = 0
    pc: int = 0
    instruction: int = 0
    control: int = 0

    def pack(self):
        return sum((v & 0xFFFFFFFF) << (32 * i) for i, v in enumerate(
            (self.wbbr, self.wbbr_high, self.msr, self.pc, self.instruction, self.control)))

    @classmethod
    def unpack(cls, value):
        return cls(*((value >> (32 * i)) & 0xFFFFFFFF for i in range(6)))


class OnCE:
    def __init__(self, tap, chip, timeout=2.0):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        self.tap, self.chip, self.timeout = tap, chip, timeout
        self.selected = False
        self.core_states = {}

    def select(self, opcode):
        if self.selected:
            # A PAUSE-DR -> UPDATE-DR route hands the shared TAP back to JTAGC.
            self.tap.scan_ir(10, NOREG)
            self.tap.goto("DRPAUSE")
            self.tap.goto("DRUPDATE")
            self.tap.goto("IDLE")
        self.tap.scan_ir(self.chip.jtagc_irlen, opcode)
        self.selected = True

    def read_register(self, register, bits=32):
        self.tap.scan_ir(10, READ | register)
        return self.tap.scan_dr(bits)

    def write_register(self, register, value, bits=32):
        self.tap.scan_ir(10, register)
        self.tap.scan_dr(bits, value, read=False)

    def status(self):
        return self.tap.scan_ir(10, READ | NOREG, read=True)

    def wait_debug(self, *, allow_reset=False, entering_debug=False):
        deadline = time.monotonic() + self.timeout
        while True:
            status = self.status()
            if status & 0x007 != 1 or not 0 <= status <= 0x3FF:
                raise DriverError(f"invalid OnCE scan 0x{status:03x}; check TAP selection and signal integrity")
            if status & 0x080:
                raise DriverError(f"OnCE checkstop, OSR=0x{status:03x}; core cannot execute debug instructions")
            if status & 0x100 and not entering_debug:
                raise DriverError(f"OnCE instruction execution error, OSR=0x{status:03x} "
                                  f"(DEBUG={int(bool(status & 8))}, RESET={int(bool(status & 0x40))}, "
                                  f"MCLK={int(bool(status & 0x200))}); the last single-step did not complete reliably")
            if (status & 0x008 and not status & 0x040 and
                    (not entering_debug or status & 0x200)):  # DEBUG, reset released, z0h clock running
                return status
            if allow_reset and status & 0x248 == 0x040:
                # A secondary core held in reset with no clock is quiescent.
                # Leave its OCR debug request armed in case it is released.
                return status
            if time.monotonic() >= deadline:
                raise OperationTimeout(f"core did not halt, OSR=0x{status:03x}")
            time.sleep(0.001)

    def attach(self, password=None):
        """Reset and halt. C55 password entry requires reset release before halt.

        Application/BAF code may run during that C55 interval. See the Lauterbach
        Qorivva manual, Censorship Unlock and SYStem.Option.KEYCODE.
        """
        if password is not None:
            validate_login(self.chip, password)
        c55_login = password is not None and self.chip.password_protocol == "jtag256"
        cores = self.chip.core_selectors or ((0x11,) if self.chip.key == "spc560b64" else (0x28, 0x29, 0x2A))
        self.tap.reset()
        self.selected = False
        self.tap.io.set_reset(srst=True)
        try:
            time.sleep(0.01)
            if password is not None and not c55_login:
                # Input is NVPWD0 followed by NVPWD1 in memory order. The
                # CENSOR_CTRL scan is LSB first, plus its enable bit at bit 64.
                value = int.from_bytes(password[:4], "big") | (int.from_bytes(password[4:], "big") << 32)
                self.tap.scan_ir(self.chip.jtagc_irlen, 7)
                self.tap.scan_dr(65, value | (1 << 64), read=False)
                time.sleep(0.001)
            if not c55_login:
                for opcode in cores:
                    self.select(opcode)
                    self.write_register(OCR, 0x5)  # DR | WKUP while reset asserted
        finally:
            self.tap.io.set_reset(srst=False)
        if c55_login:
            # MPC5748G cannot authenticate while external reset is asserted.
            # Flash-address word order, LSB-first within each 32-bit word.
            # Exactly 256 bits: the C90 enable bit does not apply here.
            time.sleep(0.01)
            value = sum(int.from_bytes(password[i:i + 4], "big") << (8 * i)
                        for i in range(0, 32, 4))
            self.tap.scan_ir(self.chip.jtagc_irlen, 7)
            self.tap.scan_dr(256, value, read=False)
            time.sleep(0.01)
            # No Test-Logic-Reset after loading the password: it clears JTAGC's
            # password register. One submission only, no alternate guesses.
            for opcode in cores:
                self.select(opcode)
                self.write_register(OCR, 0x5)
        self.core_states = {}
        for index, opcode in enumerate(cores):
            self.select(opcode)
            try:
                # OSR[ERR] describes the LAST single-step, not authentication.
                # No instruction has been issued by this attach. A z0h may
                # already report DEBUG|ERR (0x309); do not reject debug entry
                # for that historical status. Every new step remains strict.
                status = self.wait_debug(allow_reset=index > 0, entering_debug=not self.chip.has_mmu)
            except DriverError as exc:
                raise DriverError(f"core {index} (TAP 0x{opcode:02x}): {exc}") from exc
            held_reset = bool(status & 0x040)
            self.core_states[f"core{index}"] = {"selector": opcode, "osr": f"0x{status:03x}",
                                               "state": "held in reset" if held_reset else "halted",
                                               "prior_step_error": bool(status & 0x100)}
            if held_reset:
                continue
            self.write_register(DBCR0, 0x80000000)
            self.write_register(DBSR, 0xFFFFFFFF)
            self.write_register(OCR, 0x6)  # WKUP | FDB
        self.select(cores[0])
        self.wait_debug(entering_debug=not self.chip.has_mmu)

    def step(self, instruction, operand=0, *, force_operand=False, pc=0x40000000):
        ctl = (0x400 if force_operand else 0) | (2 if self.chip.has_mmu else 0)
        state = CpuScan(operand, 0, 0, pc - 4, instruction, ctl)
        self.write_register(CPUSCR, state.pack(), 192)
        self.tap.scan_ir(10, GO | NOREG)
        # GO takes effect on UPDATE-DR, not UPDATE-IR.
        self.tap.scan_dr(1, 0, read=False)
        try:
            self.wait_debug()
        except DriverError as exc:
            raise DriverError(f"single-step 0x{instruction:08X}, next PC 0x{pc:08X}: {exc}") from exc
        return CpuScan.unpack(self.read_register(CPUSCR, 192)).wbbr

    def nexus_read(self, register):
        self.tap.scan_ir(10, NEXUS)
        self.tap.scan_dr(8, register << 1, read=False)
        return self.tap.scan_dr(32)

    def nexus_write(self, register, value):
        self.tap.scan_ir(10, NEXUS)
        self.tap.scan_dr(8, (register << 1) | 1, read=False)
        self.tap.scan_dr(32, value, read=False)
