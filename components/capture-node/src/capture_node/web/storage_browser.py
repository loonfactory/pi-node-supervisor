"""Read-only Web projection over configured storage targets."""

from __future__ import annotations

from collections.abc import Sequence

from capture_node.core.storages import (
    Storage,
    StorageListResult,
    StorageProvider,
)


class StorageBrowser:
    """Finds configured targets and delegates storage-neutral reads."""

    def __init__(self, provider: StorageProvider) -> None:
        self._targets = {
            storage.name: storage for storage in provider.storages()
        }

    @property
    def targets(self) -> Sequence[Storage]:
        return tuple(self._targets.values())

    def list_files(
        self,
        target_name: str,
        page: int,
        page_size: int,
    ) -> StorageListResult:
        return self._target(target_name).list(page, page_size)

    def read_file(self, target_name: str, filename: str) -> bytes:
        return self._target(target_name).read(filename)

    def _target(self, target_name: str) -> Storage:
        try:
            return self._targets[target_name]
        except KeyError as exc:
            raise KeyError(f"unknown storage target: {target_name}") from exc
