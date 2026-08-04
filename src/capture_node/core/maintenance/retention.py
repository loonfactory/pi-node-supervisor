"""Retention policy execution over storage-neutral contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from capture_node.core.maintenance.options import RetentionOptions
from capture_node.core.storages import Storage, StorageProvider

_PAGE_SIZE = 50


@dataclass(frozen=True, slots=True)
class RetentionFailure:
    target: str
    name: str | None
    cause: BaseException


@dataclass(frozen=True, slots=True)
class RetentionResult:
    deleted: tuple[tuple[str, str], ...]
    failures: tuple[RetentionFailure, ...]


class StorageMaintenance:
    """Deletes expired files selected by policy, independently of capture."""

    def __init__(
        self,
        provider: StorageProvider,
        policies: tuple[RetentionOptions, ...],
    ) -> None:
        self._storages = {storage.name: storage for storage in provider.storages()}
        self._policies = policies

    def run(self, now: datetime | None = None) -> RetentionResult:
        observed_at = now or datetime.now(UTC)
        if observed_at.tzinfo is None:
            raise ValueError("maintenance time must be timezone-aware")
        deleted: list[tuple[str, str]] = []
        failures: list[RetentionFailure] = []
        for policy in self._policies:
            storage = self._storages[policy.target]
            cutoff = observed_at - timedelta(days=policy.max_age_days)
            try:
                self._apply_policy(storage, cutoff, deleted, failures)
            except BaseException as exc:
                failures.append(RetentionFailure(storage.name, None, exc))
        return RetentionResult(tuple(deleted), tuple(failures))

    @staticmethod
    def _apply_policy(
        storage: Storage,
        cutoff: datetime,
        deleted: list[tuple[str, str]],
        failures: list[RetentionFailure],
    ) -> None:
        failed_names: set[str] = set()
        try:
            first = storage.list(1, _PAGE_SIZE)
        except BaseException as exc:
            failures.append(RetentionFailure(storage.name, None, exc))
            return
        page = _last_page(first.total)
        while page >= 1:
            try:
                listing = storage.list(page, _PAGE_SIZE)
            except BaseException as exc:
                failures.append(RetentionFailure(storage.name, None, exc))
                return
            expired = [
                item
                for item in listing.items
                if item.modified_at < cutoff and item.name not in failed_names
            ]
            removed = 0
            for item in expired:
                try:
                    storage.delete(item.name)
                except BaseException as exc:
                    failed_names.add(item.name)
                    failures.append(RetentionFailure(storage.name, item.name, exc))
                else:
                    removed += 1
                    deleted.append((storage.name, item.name))
            if removed:
                try:
                    page = _last_page(storage.list(1, _PAGE_SIZE).total)
                except BaseException as exc:
                    failures.append(RetentionFailure(storage.name, None, exc))
                    return
            else:
                page -= 1


def _last_page(total: int) -> int:
    return (total + _PAGE_SIZE - 1) // _PAGE_SIZE
