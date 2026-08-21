"""Local managed-node registry."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path

from .errors import PlanningError
from .models import (
    NodeDefinition,
    NodeRegistryDocument,
    SensorSerialSettings,
    SensorStorageSettings,
)


class NodeRegistry:
    def __init__(
        self,
        document: NodeRegistryDocument,
        *,
        source_path: Path | None = None,
    ) -> None:
        self.document = document
        self.source_path = source_path
        self.restart_required = False
        self._lock = threading.Lock()
        self._nodes: Mapping[str, NodeDefinition] = {
            node.id: node for node in document.nodes
        }

    @classmethod
    def load(cls, path: Path) -> "NodeRegistry":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PlanningError(f"cannot read node registry {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise PlanningError("node registry root must be an object")
        return cls(NodeRegistryDocument.model_validate(raw), source_path=path)

    def require(self, node_id: str) -> NodeDefinition:
        try:
            return self._nodes[node_id]
        except KeyError as exc:
            raise PlanningError(f"activity references unknown node: {node_id}") from exc

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def update_sensor_settings(
        self,
        node_id: str,
        *,
        serial: SensorSerialSettings,
        storage: SensorStorageSettings,
    ) -> NodeDefinition:
        """Atomically stage validated sensor settings for the next service start."""

        if self.source_path is None:
            raise PlanningError("node registry was not loaded from a writable file")
        with self._lock:
            try:
                original = self.source_path.read_bytes()
                current = NodeRegistryDocument.model_validate(
                    json.loads(original.decode("utf-8"))
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PlanningError(
                    f"cannot read node registry {self.source_path}: {exc}"
                ) from exc

            selected = next((node for node in current.nodes if node.id == node_id), None)
            if selected is None:
                raise PlanningError(f"sensor node does not exist: {node_id}")
            if selected.kind != "sensor-node":
                raise PlanningError(f"node is not a sensor node: {node_id}")
            updated = selected.model_copy(
                update={"sensor_serial": serial, "sensor_storage": storage}
            )
            document = NodeRegistryDocument(
                schema_version=current.schema_version,
                nodes=tuple(updated if node.id == node_id else node for node in current.nodes),
            )
            content = (
                json.dumps(
                    document.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            backup = self.source_path.with_name(f"{self.source_path.name}.previous")
            try:
                self._atomic_write(backup, original)
                self._atomic_write(self.source_path, content)
            except OSError as exc:
                raise PlanningError(
                    f"cannot update node registry {self.source_path}: {exc}"
                ) from exc
            self.document = document
            self._nodes = {node.id: node for node in document.nodes}
            self.restart_required = True
            return updated

    def __iter__(self):
        return iter(self.document.nodes)
