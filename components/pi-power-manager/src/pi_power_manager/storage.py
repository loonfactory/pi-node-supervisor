from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, TypeVar

from .errors import StorageError, ValidationError
from .models import Configuration, RuntimeState

T = TypeVar("T")


class JsonStore:
    def __init__(self, config_path: Path, state_path: Path):
        self.config_path = config_path
        self.state_path = state_path

    def load_config(self) -> Configuration:
        if not self.config_path.exists():
            return Configuration()
        return self._load_with_backup(self.config_path, Configuration.from_dict)

    def save_config(self, configuration: Configuration) -> None:
        self._save(self.config_path, configuration.to_dict())

    def load_state(self) -> RuntimeState:
        if not self.state_path.exists():
            return RuntimeState()
        try:
            return self._load_with_backup(self.state_path, RuntimeState.from_dict)
        except StorageError as exc:
            return RuntimeState(
                automatic_shutdown_enabled=False,
                last_error={"stage": "state_load", "reason": str(exc)},
            )

    def save_state(self, state: RuntimeState) -> None:
        self._save(self.state_path, state.to_dict())

    def _load_with_backup(self, path: Path, factory: Callable[[dict[str, Any]], T]) -> T:
        errors: list[str] = []
        for candidate in (path, self._backup_path(path)):
            if not candidate.exists():
                continue
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValidationError("root value must be an object")
                return factory(raw)
            except (OSError, json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
                errors.append(f"{candidate}: {exc}")
        detail = "; ".join(errors) if errors else "no readable file or backup"
        raise StorageError(f"could not load {path}: {detail}")

    def _save(self, path: Path, data: dict[str, Any]) -> None:
        serialized = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        try:
            if path.exists():
                self._atomic_write(self._backup_path(path), path.read_text(encoding="utf-8"))
            self._atomic_write(path, serialized)
        except OSError as exc:
            raise StorageError(f"could not atomically save {path}: {exc}") from exc

    @staticmethod
    def _backup_path(path: Path) -> Path:
        return path.with_suffix(path.suffix + ".bak")

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary_path.unlink(missing_ok=True)

