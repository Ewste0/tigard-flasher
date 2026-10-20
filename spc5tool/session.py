"""Hardware session lifetime. Opening a session resets and halts the target."""
from .errors import DriverError
from .idcode import decode
from .mpsse import MpsseTransport, DEFAULT_URL
from .tap import JtagTap
from .once import OnCE
from .memory import OnceMemory, NexusMemory
from .flash import Flash
from .security import validate_login, login_result


class TargetSession:
    def __init__(self, chip, url=DEFAULT_URL, frequency=500000, expected_id=None, *,
                 reset_pin="srst", password=None, prepare_flash=True):
        self.chip, self.url, self.frequency = chip, url, frequency
        self.expected_id = expected_id
        self.reset_pin = reset_pin
        self.transport = None
        if password is not None:
            validate_login(chip, password)
        self.password = password
        self.authentication = None
        self.prepare_flash = prepare_flash

    def __enter__(self):
        self.transport = MpsseTransport.open(self.url, self.frequency, reset_pin=self.reset_pin)
        try:
            tap = JtagTap(self.transport)
            self.idcode = decode(tap.read_idcode())
            if not self.idcode.valid_ieee:
                raise DriverError(str(self.idcode))
            if self.chip.idcodes and self.idcode.raw not in self.chip.idcodes:
                raise DriverError(f"IDCODE mismatch for {self.chip.part}: 0x{self.idcode.raw:08x}")
            if self.expected_id is not None and self.idcode.raw != self.expected_id:
                raise DriverError(f"IDCODE mismatch: expected 0x{self.expected_id:08x}, got 0x{self.idcode.raw:08x}")
            once = OnCE(tap, self.chip)
            if self.password is None:
                once.attach()
            else:
                once.attach(self.password)
            self.memory = NexusMemory(once) if self.chip.has_mmu else OnceMemory(once)
            if isinstance(self.memory, OnceMemory):
                self.memory.initialize()
            if self.password is not None:
                self.authentication = login_result(self.memory, self.chip, self.password)
            self.flash = Flash(self.memory, self.chip)
            self.flash.authentication = self.authentication
            if not self.prepare_flash:
                # Authentication needs debug/status access only. Firmware can
                # lock flash-buffer settings before a post-reset C55 halt;
                # those settings are irrelevant to checking the password.
                return self
            for base in self.chip.watchdogs:
                control = self.memory.read32(base)
                self.memory.write32(base + 0x10, 0xC520)
                self.memory.write32(base + 0x10, 0xD928)
                self.memory.write32(base, control & ~1)
                if self.memory.read32(base) & 1:
                    raise DriverError(f"watchdog at 0x{base:08x} remains enabled")
            # Disable PFlash line buffering/prefetch so read-back sees the array.
            registers = self.chip.buffering
            if self.chip.read_only:
                # Reset-and-halt followed by physical Nexus reads; no array
                # modifications or programming cache setup are needed.
                registers = ()
            for address, mask in registers:
                self.memory.write32(address, self.memory.read32(address) & ~mask)
                if self.memory.read32(address) & mask:
                    raise DriverError("cannot disable flash read buffering")
            return self
        except BaseException:
            self.transport.close()
            raise

    def __exit__(self, *exc):
        # Never resume/reset automatically after an incomplete flash operation.
        self.transport.close()
