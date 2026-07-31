from __future__ import annotations

import io
import logging
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from capture_node.core.cameras import Capture, PreviewFrame
from capture_node.core.config import Configuration, JsonOptionsRepository
from capture_node.core.options import Options
from capture_node.core.events import EVENT_ATTRIBUTE, EventEmitter
from capture_node.core.storages import SaveResult


class FakeCamera:
    def __init__(
        self,
        *,
        capture_failures: int = 0,
        delay: float = 0,
        apply_failure: BaseException | None = None,
    ) -> None:
        self.capture_failures = capture_failures
        self.delay = delay
        self.apply_failure = apply_failure
        self.capture_calls = 0
        self.applied: list[dict[str, Any]] = []

    def capture(self) -> Capture:
        self.capture_calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.capture_failures:
            self.capture_failures -= 1
            raise RuntimeError("injected capture failure")
        return Capture(
            Image.new("RGB", (24, 16), (20, 110, 180)),
            datetime.now(UTC),
            {"ExposureTime": 12_000, "AnalogueGain": 1.5, "Frame": self.capture_calls},
        )

    def apply(self, parameters: Mapping[str, Any]) -> None:
        if self.apply_failure is not None:
            failure = self.apply_failure
            self.apply_failure = None
            raise failure
        self.applied.append(dict(parameters))


class PreviewCamera(FakeCamera):
    def frames(self) -> Iterator[PreviewFrame]:
        output = BytesIO()
        Image.new("RGB", (8, 6), (220, 30, 40)).save(output, "JPEG")
        while True:
            yield PreviewFrame(output.getvalue())


class FakeCameraProvider:
    def __init__(self, camera: FakeCamera) -> None:
        self._camera = camera

    def camera(self) -> FakeCamera:
        return self._camera

    def close(self) -> None:
        close = getattr(self._camera, "close", None)
        if close is not None:
            close()


class FakeStorage:
    def __init__(
        self,
        name: str,
        *,
        result: SaveResult | None = None,
        exception: BaseException | None = None,
        delay: float = 0,
    ) -> None:
        self._name = name
        self.result = result or SaveResult.success()
        self.exception = exception
        self.delay = delay
        self.calls: list[tuple[bytes, str]] = []

    @property
    def name(self) -> str:
        return self._name

    def save(self, data: bytes, name: str) -> SaveResult:
        self.calls.append((data, name))
        if self.delay:
            time.sleep(self.delay)
        if self.exception is not None:
            raise self.exception
        return self.result

    def read(self, name: str) -> bytes:
        raise io.UnsupportedOperation(f"storage {self.name!r} does not support reads")

    def list(self, page: int, page_size: int):
        raise io.UnsupportedOperation(f"storage {self.name!r} does not support listing")


class FakeStorageProvider:
    def __init__(self, *storages: FakeStorage) -> None:
        self._storages = storages

    def storages(self):
        return self._storages


class EventCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        event = getattr(record, EVENT_ATTRIBUTE, None)
        if event is not None:
            self.items.append(event)


@pytest.fixture
def event_output() -> tuple[EventEmitter, EventCollector]:
    logger = logging.Logger("capture-node-test")
    collector = EventCollector()
    logger.addHandler(collector)
    logger.setLevel(logging.DEBUG)
    return EventEmitter(logger), collector


def make_configuration(
    path: Path,
    document: dict[str, Any] | None = None,
) -> Configuration:
    options = Options.model_validate(
        document
        or {
            "capture": {"interval_seconds": 60, "params": {}},
            "image": {
                "format": "jpeg",
                "quality": 90,
                "embed_metadata": True,
            },
            "storages": [],
        }
    )
    repository = JsonOptionsRepository(path)
    repository.write(options)
    return Configuration(repository, options)


def wait_until(predicate, *, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not met before timeout")
        time.sleep(0.005)
