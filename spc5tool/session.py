"""Hardware session lifetime. Opening a session resets and halts the target."""
from .errors import DriverError
from .idcode import decode
from .mpsse import MpsseTransport, MpsseError, DEFAULT_URL
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

    def _attach(self, once):
        if self.password is not None and self.chip.password_protocol == "jtag256":
            # Customer Delivery already permits debug access. Halt under reset
            # before inspecting it: running BAF/application code just to submit
            # a password can leave BFEN set and prevent flash preparation.
            status = None
            try:
                once.attach()
                memory = NexusMemory(once)
                status = memory.read32(self.chip.flash_ctrl["PASS"] + self.chip.unlock["PASS_LCSTAT"])
            except MpsseError:
                raise  # USB failures are not an indication of censorship.
            except DriverError:
                # A protected target may reject ordinary debug access. Use the
                # supplied password once through the existing C55 scan path.
                pass
            if status is not None and status & 7 == 3:
                # Outside the fallback's try block: a mismatch must fail, never
                # retry a password scan or continue into flash setup.
                self.authentication = login_result(
                    memory, self.chip, self.password, status=status, password_submitted=False)
                return memory
        if self.password is None:
            once.attach()
        else:
            once.attach(self.password)
        memory = NexusMemory(once) if self.chip.has_mmu else OnceMemory(once)
        if isinstance(memory, OnceMemory):
            memory.initialize()
        if self.password is not None:
            self.authentication = login_result(memory, self.chip, self.password)
        return memory

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
            self.memory = self._attach(once)
            self.flash = Flash(self.memory, self.chip)
            self.flash.authentication = self.authentication
            if not self.prepare_flash:
                # Authentication needs debug/status access only.
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
                before = self.memory.read32(address)
                self.memory.write32(address, before & ~mask)
                after = self.memory.read32(address)
                if after & mask:
                    raise DriverError(
                        f"cannot disable flash read buffering at 0x{address:08X}: "
                        f"before=0x{before:08X}, after=0x{after:08X}, mask=0x{mask:08X}. "
                        "The target is halted, but buffer controls did not clear. "
                        "Flash preparation stopped before any erase or programming.")
            return self
        except BaseException:
            self.transport.close()
            raise

    def __exit__(self, *exc):
        # Never resume/reset automatically after an incomplete flash operation.
        self.transport.close()
