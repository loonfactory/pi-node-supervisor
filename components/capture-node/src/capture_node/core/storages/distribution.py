"""Multi-target distribution and binary terminal-state decisions."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum

from capture_node.core.storages.contracts import Storage, StorageProvider


class TerminalState(StrEnum):
    COMPLETED = "completed"
    DISCARDED = "discarded"


@dataclass(frozen=True, slots=True)
class DistributionArtifact:
    data: bytes
    name: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("distribution artifact name must not be empty")


@dataclass(frozen=True, slots=True)
class TargetFailure:
    target: str
    cause: BaseException
    name: str | None = None


class DistributionInProgress(RuntimeError):
    """Raised when the bounded distributor already owns one save operation."""


@dataclass(frozen=True, slots=True)
class DistributionResult:
    state: TerminalState
    targets: tuple[str, ...]
    failures: tuple[TargetFailure, ...]
    artifacts: tuple[str, ...] = ()

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

        return self.submit_many((DistributionArtifact(data, name),))

    def submit_many(
        self,
        artifacts: Sequence[DistributionArtifact],
    ) -> Future[DistributionResult]:
        """Distribute one capture's complete artifact bundle as one pending job."""

        bundle = tuple(artifacts)
        self._validate_bundle(bundle)

        with self._state_lock:
            if self._closed:
                raise RuntimeError("distributor is closed")
            if self._active:
                raise DistributionInProgress(
                    "a storage distribution is already in progress"
                )
            self._active = True
            try:
                future = self._executor.submit(self.distribute_many, bundle)
            except BaseException:
                self._active = False
                raise
        future.add_done_callback(self._release)
        return future

    def distribute(self, data: bytes, name: str) -> DistributionResult:
        return self.distribute_many((DistributionArtifact(data, name),))

    def distribute_many(
        self,
        artifacts: Sequence[DistributionArtifact],
    ) -> DistributionResult:
        bundle = tuple(artifacts)
        self._validate_bundle(bundle)
        targets = self.targets
        failures: list[TargetFailure] = []
        for target in targets:
            for artifact in bundle:
                try:
                    result = target.save(artifact.data, artifact.name)
                    if not result.succeeded:
                        assert result.cause is not None
                        failures.append(
                            TargetFailure(target.name, result.cause, artifact.name)
                        )
                except BaseException as exc:
                    failures.append(TargetFailure(target.name, exc, artifact.name))

        names = tuple(target.name for target in targets)
        artifact_names = tuple(artifact.name for artifact in bundle)
        if failures:
            return DistributionResult(
                TerminalState.DISCARDED,
                names,
                tuple(failures),
                artifact_names,
            )
        return DistributionResult(
            TerminalState.COMPLETED,
            names,
            (),
            artifact_names,
        )

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

    @staticmethod
    def _validate_bundle(artifacts: tuple[DistributionArtifact, ...]) -> None:
        if not artifacts:
            raise ValueError("distribution artifact bundle must not be empty")
        names = [artifact.name for artifact in artifacts]
        if len(names) != len(set(names)):
            raise ValueError("distribution artifact names must be unique")
