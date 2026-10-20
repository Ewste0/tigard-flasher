"""OnCE register protocol from NXP AN4365 sections 2 and 3.

Power Architecture drawings often number the MSB as zero. OCR DR is integer
bit 0, WKUP bit 2; OSR DEBUG is bit 3, ERR bit 8. All scans are LSB first.
"""
import time
import math
from dataclasses import dataclass

from .errors import DriverError, OperationTimeout

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

    def wait_debug(self, *, allow_reset=False):
        deadline = time.monotonic() + self.timeout
        while True:
            status = self.status()
            if status & 0x007 != 1 or status & 0x180:
                raise DriverError(f"invalid/error OnCE status 0x{status:03x}; check TAP, reset and censorship")
            if status & 0x008 and not status & 0x040:  # DEBUG, and reset released
                return status
            if allow_reset and status & 0x248 == 0x040:
                # A secondary core held in reset with no clock is quiescent.
                # Leave its OCR debug request armed in case it is released.
                return status
            if time.monotonic() >= deadline:
                raise OperationTimeout(f"core did not halt, OSR=0x{status:03x}")
            time.sleep(0.001)

    def attach(self, password=None):
        """Reset-and-halt session. Firmware is never automatically resumed."""
        cores = self.chip.core_selectors or ((0x11,) if self.chip.key == "spc560b64" else (0x28, 0x29, 0x2A))
        self.tap.reset()
        self.selected = False
        self.tap.io.set_reset(srst=True)
        try:
            time.sleep(0.01)
            if password is not None:
                if self.chip.algorithm != "c90-b" or len(password) != 8:
                    raise ValueError("JTAG password scan for this family is not yet verified")
                # Input is NVPWD0 followed by NVPWD1 in memory order. The
                # CENSOR_CTRL scan is LSB first, plus its enable bit at bit 64.
                value = int.from_bytes(password[:4], "big") | (int.from_bytes(password[4:], "big") << 32)
                self.tap.scan_ir(self.chip.jtagc_irlen, 7)
                self.tap.scan_dr(65, value | (1 << 64), read=False)
                time.sleep(0.001)
            for opcode in cores:
                self.select(opcode)
                self.write_register(OCR, 0x5)  # DR | WKUP while reset asserted
        finally:
            self.tap.io.set_reset(srst=False)
        self.core_states = {}
        for index, opcode in enumerate(cores):
            self.select(opcode)
            try:
                status = self.wait_debug(allow_reset=index > 0)
            except DriverError as exc:
                raise DriverError(f"core {index} (TAP 0x{opcode:02x}): {exc}") from exc
            held_reset = bool(status & 0x040)
            self.core_states[f"core{index}"] = {"selector": opcode, "osr": f"0x{status:03x}",
                                               "state": "held in reset" if held_reset else "halted"}
            if held_reset:
                continue
            self.write_register(DBCR0, 0x80000000)
            self.write_register(DBSR, 0xFFFFFFFF)
            self.write_register(OCR, 0x6)  # WKUP | FDB
        self.select(cores[0])
        self.wait_debug()

    def step(self, instruction, operand=0, *, force_operand=False, pc=0x40000000):
        ctl = (0x400 if force_operand else 0) | (2 if self.chip.has_mmu else 0)
        state = CpuScan(operand, 0, 0, pc - 4, instruction, ctl)
        self.write_register(CPUSCR, state.pack(), 192)
        self.tap.scan_ir(10, GO | NOREG)
        # GO takes effect on UPDATE-DR, not UPDATE-IR.
        self.tap.scan_dr(1, 0, read=False)
        self.wait_debug()
        return CpuScan.unpack(self.read_register(CPUSCR, 192)).wbbr

    def nexus_read(self, register):
        self.tap.scan_ir(10, NEXUS)
        self.tap.scan_dr(8, register << 1, read=False)
        return self.tap.scan_dr(32)

    def nexus_write(self, register, value):
        self.tap.scan_ir(10, NEXUS)
        self.tap.scan_dr(8, (register << 1) | 1, read=False)
        self.tap.scan_dr(32, value, read=False)
