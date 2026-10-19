"""Errors shared by hardware and offline backends."""


class DriverError(RuntimeError):
    pass


class OperationTimeout(DriverError):
    pass


class VerificationError(DriverError):
    pass
