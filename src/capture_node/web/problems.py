"""Minimal RFC 9457 problem model for the private Web API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ApiProblem(Exception):
    status: int
    code: str
    title: str
    detail: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.detail)

    def document(self, instance: str) -> dict[str, Any]:
        return {
            "type": f"/problems/{self.code.replace('_', '-')}",
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": instance,
            "code": self.code,
        }
