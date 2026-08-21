"""Immutable storage registration and lookup."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Callable, Generic, TypeVar, cast

from capture_node.core.models import StrictModel
from capture_node.core.storages.contracts import Storage
from capture_node.core.storages.options import (
    FileStorageOptions,
    FtpsStorageOptions,
    SftpStorageOptions,
    StorageOptions,
)

OptionsT = TypeVar("OptionsT", bound=StrictModel)


@dataclass(frozen=True, slots=True)
class StorageRegistration(Generic[OptionsT]):
    """Binds one configuration key and model to a Storage constructor."""

    key: str
    options_type: type[OptionsT]
    factory: Callable[[OptionsT], Storage]

    def create(self, options: StorageOptions) -> Storage:
        if not isinstance(options, self.options_type):
            raise TypeError(
                f"{self.key} requires {self.options_type.__name__}, "
                f"got {type(options).__name__}"
            )
        return self.factory(cast(OptionsT, options))


class StorageRegistry:
    """Resolves Storage registrations by validated configuration key."""

    def __init__(self, registrations: Sequence[StorageRegistration]) -> None:
        indexed: dict[str, StorageRegistration] = {}
        for registration in registrations:
            if not registration.key:
                raise ValueError("storage registration key must not be empty")
            if registration.key in indexed:
                raise ValueError(
                    f"storage registration already exists: {registration.key}"
                )
            indexed[registration.key] = registration
        self._registrations = MappingProxyType(indexed)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self._registrations)

    def create(self, options: StorageOptions) -> Storage:
        try:
            registration = self._registrations[options.type]
        except KeyError as exc:
            raise ValueError(f"unknown storage type: {options.type}") from exc
        return registration.create(options)


@lru_cache(maxsize=1)
def default_storage_registry() -> StorageRegistry:
    """Return the immutable registry shipped with CaptureNode."""

    from capture_node.core.storages.file_storage import FileStorage
    from capture_node.core.storages.ftps_storage import FtpsStorage
    from capture_node.core.storages.sftp_storage import SftpStorage

    return StorageRegistry(
        (
            StorageRegistration(
                "file",
                FileStorageOptions,
                FileStorage.from_options,
            ),
            StorageRegistration(
                "sftp",
                SftpStorageOptions,
                SftpStorage.from_options,
            ),
            StorageRegistration(
                "ftps",
                FtpsStorageOptions,
                FtpsStorage.from_options,
            ),
        )
    )
