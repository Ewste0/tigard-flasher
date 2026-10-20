"""Errors shared by hardware and offline backends."""


class DriverError(RuntimeError):
    pass


class NexusBusError(DriverError):
    def __init__(self, operation, address, status):
        self.operation, self.address, self.status = operation, address, status
        super().__init__(f"Nexus bus error during {operation} at 0x{address:08X}: RWCS=0x{status:08X}")


class OperationTimeout(DriverError):
    pass


class VerificationError(DriverError):
    pass
