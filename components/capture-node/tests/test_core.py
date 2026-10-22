from __future__ import annotations

import json
import os
import time
import logging
import statistics
import threading
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path
from io import BytesIO

import pytest
from PIL import Image

from capture_node.core import CaptureNode
from capture_node.core.events import EventEmitter, EventType
from capture_node.core.storages import SaveResult
from capture_node.core.storages.file_storage import FileStorage
from conftest import (
    FakeCamera,
    FakeCameraProvider,
    FakeStorageProvider,
    FakeStorage,
    make_configuration,
    wait_until,
)


def build_core(
    tmp_path: Path,
    camera: FakeCamera,
    storages: tuple[FakeStorage, ...],
    event_output,
    *,
    interval: float = 60,
    params: dict | None = None,
) -> tuple[CaptureNode, list[dict]]:
    emitter, collector = event_output
    configuration = make_configuration(
        tmp_path / "config.json",
        {
            "camera": {"type": "picamera2", "params": params or {}},
            "captures": [{"type": "interval", "interval": interval}],
            "images": {
                "jpeg": {
                    "quality": 90,
                    "embedMetadata": True,
                },
            },
            "storages": [],
        },
    )
    node = CaptureNode(
        FakeCameraProvider(camera),
        configuration,
        FakeStorageProvider(*storages),
        emitter=emitter,
    )
    return node, collector.items


@pytest.mark.covers(
    "CAP-001",
    "CAP-002",
    "STO-001",
    "STO-003",
    "STA-001",
    "IF-CAP-001",
    "NFR-003",
)
def test_b1_normal_collection_reaches_completed_terminal_state(
    tmp_path: Path,
    event_output,
) -> None:
    storage = FakeStorage("local")
    node, emitted = build_core(tmp_path, FakeCamera(), (storage,), event_output)

    result = node.collect_once()
    wait_until(
        lambda: any(
            item["eventType"] == EventType.TERMINAL_COMPLETED for item in emitted
        )
    )

    assert result.accepted
    assert result.error is None
    assert result.captured_at is not None
    assert len(storage.calls) == 1
    assert [item["eventType"] for item in emitted] == [
        EventType.CAPTURE_SUCCEEDED,
        EventType.TERMINAL_COMPLETED,
    ]


def test_png_jpeg_and_dng_are_distributed_as_one_capture_bundle(
    tmp_path: Path,
    event_output,
) -> None:
    storage = FakeStorage("local")
    node, emitted = build_core(tmp_path, FakeCamera(), (storage,), event_output)
    document = node.options.model_dump(mode="json")
    document["images"] = {
        "png": {"compression": 7, "embedMetadata": True},
        "jpeg": {"quality": 90, "embedMetadata": True},
        "dng": {"embedMetadata": True},
    }
    assert node.update_options(document).succeeded

    result = node.collect_once()
    wait_until(
        lambda: any(
            item["eventType"] == EventType.TERMINAL_COMPLETED for item in emitted
        )
    )

    assert result.error is None
    assert len(storage.calls) == 3
    names = [name for _data, name in storage.calls]
    assert names[0].endswith(".png")
    assert names[1].endswith(".jpeg")
    assert names[2].endswith(".dng")
    stems = {
        name.removesuffix(extension)
        for name, extension in zip(names, (".png", ".jpeg", ".dng"), strict=True)
    }
    assert len(stems) == 1
    assert storage.calls[2][0] == b"fake-dng"
    png_parameters = json.loads(
        Image.open(BytesIO(storage.calls[0][0])).getexif()[37510][8:]
    )
    assert png_parameters["captureId"] == result.capture_id
    completed = next(
        item for item in emitted if item["eventType"] == EventType.TERMINAL_COMPLETED
    )
    assert completed["artifacts"] == names


def test_dng_only_capture_distributes_only_the_camera_raw_artifact(
    tmp_path: Path,
    event_output,
) -> None:
    storage = FakeStorage("local")
    node, emitted = build_core(tmp_path, FakeCamera(), (storage,), event_output)
    document = node.options.model_dump(mode="json")
    document["images"] = {"dng": {"embedMetadata": True}}
    assert node.update_options(document).succeeded

    result = node.collect_once()
    wait_until(
        lambda: any(
            item["eventType"] == EventType.TERMINAL_COMPLETED for item in emitted
        )
    )

    assert result.error is None
    assert len(storage.calls) == 1
    data, name = storage.calls[0]
    assert data == b"fake-dng"
    assert name.endswith(".dng")
    completed = next(
        item for item in emitted if item["eventType"] == EventType.TERMINAL_COMPLETED
    )
    assert completed["artifacts"] == [name]


@pytest.mark.covers("STO-001", "STO-003")
def test_b2_zero_target_collection_completes(
    tmp_path: Path,
    event_output,
) -> None:
    node, emitted = build_core(tmp_path, FakeCamera(), (), event_output)

    result = node.collect_once()

    assert result.error is None
    assert emitted[-1]["targets"] == []


@pytest.mark.covers("CAP-005", "IF-CAP-002", "NFR-003", "NFR-005")
def test_c1_capture_failure_is_observable_and_next_cycle_continues(
    tmp_path: Path,
    event_output,
) -> None:
    camera = FakeCamera(capture_failures=1)
    node, emitted = build_core(tmp_path, camera, (), event_output)

    first = node.collect_once()
    second = node.collect_once()

    assert isinstance(first.error, RuntimeError)
    assert second.error is None
    failure = emitted[0]
    assert failure["eventType"] == EventType.CAPTURE_FAILED
    assert failure["cause"]["code"] == "RuntimeError"
    assert "injected" in failure["cause"]["message"]


@pytest.mark.covers("STO-004", "STO-005", "NFR-005")
def test_c2_storage_failure_emits_discard_with_cause(
    tmp_path: Path,
    event_output,
) -> None:
    storage = FakeStorage(
        "offline",
        result=SaveResult.failure(ConnectionError("offline")),
    )
    node, emitted = build_core(tmp_path, FakeCamera(), (storage,), event_output)

    result = node.collect_once()
    wait_until(
        lambda: any(
            item["eventType"] == EventType.TERMINAL_DISCARDED for item in emitted
        )
    )

    assert result.error is None
    terminal = emitted[-1]
    assert terminal["eventType"] == EventType.TERMINAL_DISCARDED
    assert terminal["failures"][0]["target"] == "offline"
    assert terminal["failures"][0]["cause"]["code"] == "ConnectionError"


@pytest.mark.covers("CAP-010", "NFR-003", "NFR-007")
def test_c5_delayed_storage_skips_due_ticks_without_overlap(
    tmp_path: Path,
    event_output,
) -> None:
    storage = FakeStorage("slow", delay=0.11)
    node, emitted = build_core(
        tmp_path,
        FakeCamera(),
        (storage,),
        event_output,
        interval=0.03,
    )

    node.start()
    time.sleep(0.19)
    node.stop()

    event_types = [item["eventType"] for item in emitted]
    assert EventType.CAPTURE_SKIPPED in event_types
    assert all(item["pendingElapsed"] >= 0 for item in emitted if item["eventType"] == EventType.CAPTURE_SKIPPED)
    assert len(storage.calls) == 2


@pytest.mark.covers("CAP-010")
def test_c6_collection_resumes_after_delayed_terminal_state(
    tmp_path: Path,
    event_output,
) -> None:
    storage = FakeStorage("slow", delay=0.06)
    node, emitted = build_core(
        tmp_path,
        FakeCamera(),
        (storage,),
        event_output,
        interval=0.025,
    )

    node.start()
    time.sleep(0.16)
    node.stop()

    completed = [
        item for item in emitted if item["eventType"] == EventType.TERMINAL_COMPLETED
    ]
    assert len(completed) >= 2


@pytest.mark.covers("NFR-002")
def test_b4_one_month_equivalent_cycles_do_not_degrade(
    tmp_path: Path,
    event_output,
) -> None:
    logger = logging.Logger("capture-node-endurance")
    logger.propagate = False
    node, _emitted = build_core(
        tmp_path,
        FakeCamera(),
        (),
        (EventEmitter(logger), event_output[1]),
        interval=3600,
    )
    for _ in range(20):
        node.collect_once()

    tracemalloc.start()
    timings: list[float] = []
    for _ in range(744):
        started = time.perf_counter()
        result = node.collect_once()
        timings.append(time.perf_counter() - started)
        assert result.error is None
    retained_bytes, _peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    first = statistics.median(timings[:100])
    last = statistics.median(timings[-100:])
    assert retained_bytes < 1_500_000
    assert last <= first * 4
    assert not node.is_pending


@pytest.mark.covers("STO-001", "NFR-003")
def test_a4_start_with_no_targets_emits_warning(
    tmp_path: Path,
    event_output,
) -> None:
    node, emitted = build_core(
        tmp_path,
        FakeCamera(),
        (),
        event_output,
        interval=10,
    )

    node.start()
    node.stop()

    assert [item["eventType"] for item in emitted] == [
        EventType.STARTED,
        EventType.NO_STORAGE_TARGETS,
        EventType.STOPPED,
    ]


def test_storage_scoped_maintenance_runs_for_its_own_target(
    tmp_path: Path,
    event_output,
) -> None:
    root = tmp_path / "captures"
    root.mkdir()
    expired = root / "expired.png"
    expired.write_bytes(b"expired")
    modified_at = datetime.now(UTC) - timedelta(days=8)
    os.utime(expired, (modified_at.timestamp(), modified_at.timestamp()))
    configuration = make_configuration(
        tmp_path / "config.json",
        {
            "captures": [{"type": "interval", "interval": 3600}],
            "storages": [
                {
                    "type": "file",
                    "name": "local",
                    "directory": str(root),
                    "layout": "flat",
                    "maintenance": {
                        "intervalSeconds": 3600,
                        "maxAgeDays": 7,
                    },
                }
            ],
        },
    )
    emitter, collector = event_output
    node = CaptureNode(
        FakeCameraProvider(FakeCamera()),
        configuration,
        FakeStorageProvider(FileStorage("local", root, "flat")),
        emitter=emitter,
    )

    node.start()
    wait_until(lambda: not expired.exists())
    node.stop()

    completed = [
        item
        for item in collector.items
        if item["eventType"] == EventType.RETENTION_COMPLETED
    ]
    assert completed[0]["deleted"] == [
        {"target": "local", "name": "expired.png"}
    ]


@pytest.mark.covers("NFR-003", "NFR-005")
def test_stop_rejects_new_work_and_drains_background_storage(
    tmp_path: Path,
    event_output,
) -> None:
    storage = FakeStorage("slow", delay=0.15)
    node, emitted = build_core(
        tmp_path,
        FakeCamera(),
        (storage,),
        event_output,
    )
    node.start()
    accepted = node.collect_once()
    stopping = threading.Thread(target=node.stop)

    stopping.start()
    wait_until(lambda: not node.is_accepting_work)

    rejected = node.collect_once()
    assert accepted.accepted
    assert not rejected.accepted
    assert stopping.is_alive()

    stopping.join(timeout=1)
    assert not stopping.is_alive()
    assert not node.is_pending
    assert [item["eventType"] for item in emitted][-2:] == [
        EventType.TERMINAL_COMPLETED,
        EventType.STOPPED,
    ]


@pytest.mark.covers("NFR-003")
def test_stopped_node_cannot_restart(
    tmp_path: Path,
    event_output,
) -> None:
    node, _emitted = build_core(
        tmp_path,
        FakeCamera(),
        (),
        event_output,
    )
    node.start()
    node.stop()

    with pytest.raises(RuntimeError, match="cannot restart"):
        node.start()


@pytest.mark.covers("CAP-004", "CFG-009", "CFG-010", "IF-CAP-005", "NFR-003")
def test_d1_node_applies_runtime_capture_settings(
    tmp_path: Path,
    event_output,
) -> None:
    camera = FakeCamera()
    node, emitted = build_core(tmp_path, camera, (), event_output)
    document = node.options.model_dump(mode="json")
    document["captures"] = [{"type": "interval", "interval": 4}]
    document["camera"]["params"] = {"Brightness": 0.4}

    result = node.update_options(document)

    assert result.succeeded
    assert camera.applied[-1] == {"Brightness": 0.4}
    assert emitted[-1]["eventType"] == EventType.CONFIG_CHANGED


@pytest.mark.covers("CAP-001", "CAP-008", "CAP-009", "CFG-009")
def test_runtime_preview_only_mode_keeps_manual_capture_and_image_policy(
    tmp_path: Path,
    event_output,
) -> None:
    camera = FakeCamera()
    storage = FakeStorage("local")
    node, _emitted = build_core(
        tmp_path,
        camera,
        (storage,),
        event_output,
    )
    document = node.options.model_dump(mode="json")
    document["captures"] = []
    document["images"] = {
        "png": {
            "compression": 7,
            "embedMetadata": False,
        },
    }

    update = node.update_options(document)
    capture = node.collect_once()
    wait_until(lambda: bool(storage.calls))

    assert update.succeeded
    assert capture.accepted
    assert capture.error is None
    assert storage.calls[0][1].endswith(".png")


@pytest.mark.covers("CFG-011")
def test_d4_camera_apply_failure_rolls_back_parameters(
    tmp_path: Path,
    event_output,
) -> None:
    camera = FakeCamera()
    node, _emitted = build_core(
        tmp_path,
        camera,
        (),
        event_output,
        params={"old": True},
    )
    camera.apply_failure = RuntimeError("rejected")
    document = node.options.model_dump(mode="json")
    document["captures"] = [{"type": "interval", "interval": 4}]
    document["camera"]["params"] = {"new": True}

    result = node.update_options(document)

    assert not result.succeeded
    assert camera.applied[-1] == {"old": True}
