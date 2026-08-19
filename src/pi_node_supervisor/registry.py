"""Local managed-node registry."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from .errors import PlanningError
from .models import NodeDefinition, NodeRegistryDocument


class NodeRegistry:
    def __init__(self, document: NodeRegistryDocument) -> None:
        self.document = document
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
        return cls(NodeRegistryDocument.model_validate(raw))

    def require(self, node_id: str) -> NodeDefinition:
        try:
            return self._nodes[node_id]
        except KeyError as exc:
            raise PlanningError(f"activity references unknown node: {node_id}") from exc

    def __iter__(self):
        return iter(self.document.nodes)

