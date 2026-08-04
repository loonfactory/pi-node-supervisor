"""Configuration persistence and serialized runtime replacement."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from capture_node.core.cameras.options import CameraOptions
from capture_node.core.options import CaptureOptions, Options


class ConfigurationError(RuntimeError):
    """Raised when a configuration document cannot be loaded."""


class UnsupportedRuntimeChange(ValueError):
    """Raised when immutable runtime configuration is changed."""


@dataclass(frozen=True, slots=True)
class UpdateResult:
    """Outcome of a runtime configuration replacement."""

    succeeded: bool
    options: Options
    cause: BaseException | None = None


class JsonOptionsRepository:
    """Filesystem adapter for a single JSON options document."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load_document(self) -> Mapping[str, Any]:
        if not self.path.is_file():
            raise ConfigurationError(f"configuration file not found: {self.path}")
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(f"cannot read configuration: {exc}") from exc
        if not isinstance(document, dict):
            raise ConfigurationError("configuration root must be a JSON object")
        return document

    def write(self, options: Options) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            options.model_dump(mode="json"),
            ensure_ascii=True,
            indent=2,
        )
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.write("\n")
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, self.path)
        except BaseException:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise


class Configuration:
    """Owns the active validated options and serialized replacement flow."""

    def __init__(self, repository: JsonOptionsRepository, options: Options) -> None:
        self._repository = repository
        self._options = options
        self._lock = threading.Lock()

    @classmethod
    def load(cls, path: Path) -> Configuration:
        repository = JsonOptionsRepository(path)
        options = Options.model_validate(repository.load_document())
        return cls(repository, options)

    @property
    def current(self) -> Options:
        with self._lock:
            return self._options

    def replace(
        self,
        document: Mapping[str, Any],
        apply: Callable[[CaptureOptions], None],
    ) -> UpdateResult:
        candidate = Options.model_validate(document)
        with self._lock:
            previous = self._options
            if candidate.camera != previous.camera:
                raise UnsupportedRuntimeChange(
                    "camera configuration cannot be changed while running"
                )
            if candidate.storages != previous.storages:
                raise UnsupportedRuntimeChange(
                    "storage targets cannot be changed while running"
                )
            if candidate.maintenance != previous.maintenance:
                raise UnsupportedRuntimeChange(
                    "maintenance policy cannot be changed while running"
                )
            try:
                self._repository.write(candidate)
                apply(candidate.capture)
            except BaseException as exc:
                return UpdateResult(False, previous, exc)
            self._options = candidate
            return UpdateResult(True, candidate)

    def replace_camera(
        self,
        document: Mapping[str, Any],
        apply: Callable[[CameraOptions], None],
    ) -> UpdateResult:
        with self._lock:
            previous = self._options
            candidate_document = previous.model_dump(mode="json")
            candidate_document["camera"] = dict(document)
            candidate = Options.model_validate(candidate_document)
            try:
                self._repository.write(candidate)
                apply(candidate.camera)
            except BaseException as exc:
                return UpdateResult(False, previous, exc)
            self._options = candidate
            return UpdateResult(True, candidate)
