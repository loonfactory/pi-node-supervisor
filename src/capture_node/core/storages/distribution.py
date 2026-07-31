"""Multi-target distribution and binary terminal-state decisions."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum

from capture_node.core.storages.contracts import Storage, StorageProvider


class TerminalState(StrEnum):
    COMPLETED = "completed"
    DISCARDED = "discarded"


@dataclass(frozen=True, slots=True)
class TargetFailure:
    target: str
    cause: BaseException


class DistributionInProgress(RuntimeError):
    """Raised when the bounded distributor already owns one save operation."""


@dataclass(frozen=True, slots=True)
class DistributionResult:
    state: TerminalState
    targets: tuple[str, ...]
    failures: tuple[TargetFailure, ...]

    def __post_init__(self) -> None:
        if self.state is TerminalState.COMPLETED and self.failures:
            raise ValueError("completed distribution cannot contain failures")
        if self.state is TerminalState.DISCARDED and not self.failures:
            raise ValueError("discarded distribution requires a failure")


class Distributor:
    """Attempts every target once and owns one background save operation."""

    def __init__(self, provider: StorageProvider) -> None:
        self._provider = provider
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="capture-node-storage",
        )
        self._state_lock = threading.Lock()
        self._active = False
        self._closed = False

    @property
    def targets(self) -> tuple[Storage, ...]:
        return tuple(self._provider.storages())

    def submit(self, data: bytes, name: str) -> Future[DistributionResult]:
        """Start one bounded background distribution without waiting for storage."""

        with self._state_lock:
            if self._closed:
                raise RuntimeError("distributor is closed")
            if self._active:
                raise DistributionInProgress(
                    "a storage distribution is already in progress"
                )
            self._active = True
            try:
                future = self._executor.submit(self.distribute, data, name)
            except BaseException:
                self._active = False
                raise
        future.add_done_callback(self._release)
        return future

    def distribute(self, data: bytes, name: str) -> DistributionResult:
        targets = self.targets
        failures: list[TargetFailure] = []
        for target in targets:
            try:
                result = target.save(data, name)
                if not result.succeeded:
                    assert result.cause is not None
                    failures.append(TargetFailure(target.name, result.cause))
            except BaseException as exc:
                failures.append(TargetFailure(target.name, exc))

        names = tuple(target.name for target in targets)
        if failures:
            return DistributionResult(
                TerminalState.DISCARDED,
                names,
                tuple(failures),
            )
        return DistributionResult(TerminalState.COMPLETED, names, ())

    def close(self) -> None:
        """Wait for the active distribution and reject later submissions."""

        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=True)

    def _release(self, _future: Future[DistributionResult]) -> None:
        with self._state_lock:
            self._active = False
