class PowerManagerError(Exception):
    """Base class for expected, user-facing failures."""


class ValidationError(PowerManagerError):
    """Configuration or command input is invalid."""


class StorageError(PowerManagerError):
    """Persistent data could not be safely read or written."""


class RTCError(PowerManagerError):
    """An RTC operation failed without discarding its low-level cause."""

    def __init__(self, operation: str, path: str, cause: BaseException | str):
        self.operation = operation
        self.path = path
        self.cause = cause
        super().__init__(f"RTC {operation} failed for {path}: {cause}")


class AlarmConflictError(RTCError):
    """A wake alarm not owned by this application would be overwritten."""


class PowerRequestError(PowerManagerError):
    """The operating system rejected a power-off request."""

