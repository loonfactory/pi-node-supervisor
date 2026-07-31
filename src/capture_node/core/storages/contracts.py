"""Storage contracts and persistence results."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SaveResult:
    """Result of one storage target's single save attempt."""

    succeeded: bool
    cause: BaseException | None = None

    @classmethod
    def success(cls) -> SaveResult:
        return cls(succeeded=True)

    @classmethod
    def failure(cls, cause: BaseException) -> SaveResult:
        return cls(succeeded=False, cause=cause)

    def __post_init__(self) -> None:
        if self.succeeded and self.cause is not None:
            raise ValueError("a successful save cannot have a failure cause")
        if not self.succeeded and self.cause is None:
            raise ValueError("a failed save must have a cause")


@dataclass(frozen=True, slots=True)
class StoredFile:
    """Storage-neutral file metadata returned by a listing."""

    name: str
    size: int
    modified_at: datetime


@dataclass(frozen=True, slots=True)
class StorageListResult:
    """One page of files and the total matching entry count."""

    items: tuple[StoredFile, ...]
    total: int

    def __post_init__(self) -> None:
        if self.total < len(self.items):
            raise ValueError("storage list total cannot be smaller than its page")


class Storage(Protocol):
    """One configured target for capture persistence and retrieval."""

    @property
    def name(self) -> str:
        """Return the stable target name used in events."""

    def save(self, data: bytes, name: str) -> SaveResult:
        """Persist one named byte sequence and report within finite time."""

    def read(self, name: str) -> bytes:
        """Read one named file or raise io.UnsupportedOperation."""

    def list(self, page: int, page_size: int) -> StorageListResult:
        """List one file page or raise io.UnsupportedOperation."""


class StorageProvider(Protocol):
    """Provides the complete storage target set assembled by the host."""

    def storages(self) -> Sequence[Storage]:
        """Return registered targets in deterministic order."""
