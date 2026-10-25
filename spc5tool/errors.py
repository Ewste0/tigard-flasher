"""Errors shared by hardware and offline backends."""


class DriverError(RuntimeError):
    pass


class OnCEInstructionError(DriverError):
    """A valid OSR scan reports an instruction error (not a USB failure)."""
    def __init__(self, status):
        self.status = status
        super().__init__(f"OnCE instruction execution error, OSR=0x{status:03x} "
                         f"(DEBUG={int(bool(status & 8))}, RESET={int(bool(status & 0x40))}, "
                         f"MCLK={int(bool(status & 0x200))}); "
                         "the last single-step did not complete reliably")


class NexusBusError(DriverError):
    def __init__(self, operation, address, status):
        self.operation, self.address, self.status = operation, address, status
        super().__init__(f"Nexus bus error during {operation} at 0x{address:08X}: RWCS=0x{status:08X}")


class OperationTimeout(DriverError):
    pass


class VerificationError(DriverError):
    pass
