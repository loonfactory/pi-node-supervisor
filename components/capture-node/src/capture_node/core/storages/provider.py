"""Configuration-driven storage provider."""

from __future__ import annotations

from collections.abc import Sequence

from capture_node.core.storages.contracts import Storage
from capture_node.core.storages.options import StorageOptions
from capture_node.core.storages.registry import (
    StorageRegistry,
    default_storage_registry,
)


class ConfiguredStorageProvider:
    """Builds all configured storage targets in deterministic order."""

    def __init__(
        self,
        options: Sequence[StorageOptions],
        registry: StorageRegistry | None = None,
    ) -> None:
        registry = registry or default_storage_registry()
        self._storages: tuple[Storage, ...] = tuple(
            registry.create(option) for option in options
        )

    def storages(self) -> Sequence[Storage]:
        return self._storages
