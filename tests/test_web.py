from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi import Request

from capture_node.core import CaptureNode
from capture_node.core.cameras import (
    CameraControl,
    CameraControlsUnavailable,
    CameraDevice,
    Picamera2Options,
    V4L2CameraOptions,
)
from capture_node.core.cameras.unavailable import UnavailableCamera
from capture_node.core.events import EventEmitter, EventType, LOGGER_NAME
from capture_node.core.storages import (
    ConfiguredStorageProvider,
    FileStorageOptions,
)
from capture_node.web.event_buffer import EventBuffer
from capture_node.web.problems import ApiProblem
from capture_node.web.server import _multipart_frames, create_app
from conftest import (
    FakeCamera,
    FakeCameraProvider,
    FakeStorage,
    FakeStorageProvider,
    PreviewCamera,
    make_configuration,
    wait_until,
)


class ReplaceableCameraProvider(FakeCameraProvider):
    def __init__(self, camera) -> None:
        super().__init__(camera)
        self.replacements = []

    def replace(self, options, parameters) -> None:
        self.replacements.append((options, dict(parameters)))


class DiscoverableCameraProvider(ReplaceableCameraProvider):
    def devices(self):
        return (
            CameraDevice(
                identifier="picamera2:1",
                name="imx219",
                options=Picamera2Options(
                    camera_num=1,
                    width=1920,
                    height=1080,
                ),
            ),
            CameraDevice(
                identifier="v4l2:/dev/video7",
                name="USB Camera",
                options=V4L2CameraOptions(
                    type="v4l2",
                    device=Path("/dev/video7"),
                ),
            ),
        )


def web_runtime(tmp_path: Path, camera):
    configuration = make_configuration(tmp_path / "config.json")
    provider = FakeCameraProvider(camera)
    storage_provider = FakeStorageProvider()
    return (
        CaptureNode(provider, configuration, storage_provider),
        provider,
        storage_provider,
    )


def endpoint(app, path: str, method: str = "GET"):
    return next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", None) == path
        and method in getattr(route, "methods", ())
    )


def problem_response(app, path: str, problem: ApiProblem):
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
    )
    handler = app.exception_handlers[ApiProblem]
    return asyncio.run(handler(request, problem))


@pytest.mark.covers("WEB-001", "WEB-002", "IF-CAP-003")
def test_e1_preview_stream_returns_fake_frame(tmp_path: Path) -> None:
    camera = PreviewCamera()
    node, provider, storages = web_runtime(tmp_path, camera)
    app = create_app(
        node,
        provider,
        storages,
        manage_lifecycle=False,
    )
    response = endpoint(app, "/api/preview")()
    first = next(_multipart_frames(camera.frames()))

    assert response.media_type == "multipart/x-mixed-replace; boundary=frame"
    assert b"Content-Type: image/jpeg" in first
    assert first.startswith(b"--frame")


@pytest.mark.covers("IF-CAP-003")
def test_x3_camera_without_stream_reports_preview_unavailable(
    tmp_path: Path,
) -> None:
    camera = FakeCamera()
    node, provider, storages = web_runtime(tmp_path, camera)
    app = create_app(
        node,
        provider,
        storages,
        manage_lifecycle=False,
    )

    with pytest.raises(ApiProblem) as raised:
        endpoint(app, "/api/preview")()
    assert raised.value.status == 501


def test_web_reports_optional_camera_controls_as_unavailable(
    tmp_path: Path,
) -> None:
    class ControlsUnavailableCamera(PreviewCamera):
        def controls(self):
            raise CameraControlsUnavailable(
                "v4l2-ctl is unavailable; capture and preview remain active",
                code="v4l2_ctl_unavailable",
            )

    camera = ControlsUnavailableCamera()
    node, provider, storages = web_runtime(tmp_path, camera)
    app = create_app(node, provider, storages, manage_lifecycle=False)

    response = endpoint(app, "/api/camera/controls")()

    assert response == {
        "items": [],
        "available": False,
        "warning": {
            "code": "v4l2_ctl_unavailable",
            "message": (
                "v4l2-ctl is unavailable; capture and preview remain active"
            ),
        },
    }


def test_web_exposes_software_controls_when_hardware_tool_is_unavailable(
    tmp_path: Path,
) -> None:
    fallback = CameraControl(
        name="horizontal_flip",
        label="Horizontal Flip",
        control_type="bool",
        value=True,
        application="software",
    )

    class SoftwareControlsCamera(PreviewCamera):
        def controls(self):
            raise CameraControlsUnavailable(
                "hardware controls unavailable",
                controls=(fallback,),
            )

    camera = SoftwareControlsCamera()
    node, provider, storages = web_runtime(tmp_path, camera)
    app = create_app(node, provider, storages, manage_lifecycle=False)

    response = endpoint(app, "/api/camera/controls")()

    assert response["available"] is True
    assert response["items"][0]["name"] == "horizontal_flip"
    assert response["items"][0]["application"] == "software"
    assert response["warning"]["message"] == "hardware controls unavailable"


@pytest.mark.covers("WEB-001", "WEB-003")
def test_web_reports_disconnected_camera_and_rejects_capture(
    tmp_path: Path,
) -> None:
    camera = UnavailableCamera(ValueError("V4L2 device does not exist: /dev/video0"))
    node, provider, storages = web_runtime(tmp_path, camera)
    app = create_app(
        node,
        provider,
        storages,
        manage_lifecycle=False,
    )

    health = endpoint(app, "/api/health")()

    assert health == {
        "status": "running",
        "camera_connected": False,
        "camera_detail": "V4L2 device does not exist: /dev/video0",
    }
    with pytest.raises(ApiProblem) as preview_error:
        endpoint(app, "/api/preview")()
    assert preview_error.value.status == 503
    with pytest.raises(ApiProblem) as capture_error:
        endpoint(app, "/api/capture", "POST")()
    assert capture_error.value.status == 409


@pytest.mark.covers("WEB-005", "WEB-006")
def test_e2_web_reads_and_updates_runtime_settings(tmp_path: Path) -> None:
    camera = PreviewCamera()
    node, provider, storages = web_runtime(tmp_path, camera)
    app = create_app(
        node,
        provider,
        storages,
        manage_lifecycle=False,
    )

    document = endpoint(app, "/api/config")()
    document["capture"] = {
        "interval_seconds": 2.5,
        "params": {"Contrast": 1.2},
    }
    document["image"] = {
        "format": "png",
        "compression": 7,
        "embed_metadata": False,
        "raw": {"enabled": False, "format": "dng"},
    }
    response = asyncio.run(endpoint(app, "/api/config", "PUT")(document))

    assert response["capture"]["interval_seconds"] == 2.5
    assert response["image"] == {
        "format": "png",
        "compression": 7,
        "embed_metadata": False,
        "raw": {"enabled": False, "format": "dng"},
    }
    assert camera.applied[-1] == {"Contrast": 1.2}


@pytest.mark.covers("WEB-006", "CFG-011")
def test_e2_web_displays_update_failure_as_http_error(tmp_path: Path) -> None:
    camera = PreviewCamera()
    camera.apply_failure = RuntimeError("unsupported camera control")
    node, provider, storages = web_runtime(tmp_path, camera)
    app = create_app(
        node,
        provider,
        storages,
        manage_lifecycle=False,
    )

    document = endpoint(app, "/api/config")()
    document["capture"]["params"] = {"Invalid": 1}

    with pytest.raises(ApiProblem) as raised:
        asyncio.run(endpoint(app, "/api/config", "PUT")(document))
    assert raised.value.status == 409
    assert "unsupported camera control" in raised.value.detail


def test_web_replaces_camera_through_provider(tmp_path: Path) -> None:
    camera = PreviewCamera()
    configuration = make_configuration(tmp_path / "config.json")
    provider = ReplaceableCameraProvider(camera)
    storages = FakeStorageProvider()
    node = CaptureNode(provider, configuration, storages)
    app = create_app(node, provider, storages, manage_lifecycle=False)

    response = asyncio.run(
        endpoint(app, "/api/camera", "PUT")(
            {
                "type": "v4l2",
                "device": "/dev/video7",
                "width": 1920,
                "height": 1080,
                "frame_rate": 24,
                "startup_timeout_seconds": 10,
            }
        )
    )

    assert response["device"] == "/dev/video7"
    assert len(provider.replacements) == 1
    options, parameters = provider.replacements[0]
    assert options.device == Path("/dev/video7")
    assert parameters == {}


def test_web_returns_all_registered_camera_devices(tmp_path: Path) -> None:
    configuration = make_configuration(tmp_path / "config.json")
    provider = DiscoverableCameraProvider(PreviewCamera())
    storages = FakeStorageProvider()
    node = CaptureNode(provider, configuration, storages)
    app = create_app(node, provider, storages, manage_lifecycle=False)

    response = endpoint(app, "/api/camera/devices")()

    assert response["items"] == [
        {
            "id": "picamera2:1",
            "type": "picamera2",
            "name": "imx219",
            "options": {
                "type": "picamera2",
                "camera_num": 1,
                "width": 1920,
                "height": 1080,
                "preview_fps": 10.0,
                "preview_quality": 75,
            },
        },
        {
            "id": "v4l2:/dev/video7",
            "type": "v4l2",
            "name": "USB Camera",
            "options": {
                "type": "v4l2",
                "device": "/dev/video7",
                "width": 1920,
                "height": 1080,
                "frame_rate": 24.0,
                "startup_timeout_seconds": 10.0,
            },
        },
    ]


@pytest.mark.covers("WEB-007", "WEB-008")
def test_e3_event_buffer_retains_only_current_process_events() -> None:
    handler = EventBuffer(capacity=2)
    logger = logging.Logger("buffer-test")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    emitter = EventEmitter(logger)

    emitter.emit(EventType.STARTED)
    emitter.emit(EventType.CAPTURE_SUCCEEDED, capture_id="one")
    emitter.emit(EventType.TERMINAL_COMPLETED, capture_id="one", targets=[])

    assert [item["event_type"] for item in handler.snapshot()] == [
        EventType.TERMINAL_COMPLETED,
        EventType.CAPTURE_SUCCEEDED,
    ]
    assert handler.latest_capture_id() == "one"
    assert EventBuffer(capacity=2).snapshot() == []
    assert EventBuffer(capacity=2).latest_capture_id() is None


@pytest.mark.covers("WEB-007", "WEB-008")
def test_e3_event_buffer_paginates_with_a_bounded_page_size() -> None:
    handler = EventBuffer(capacity=25)
    logger = logging.Logger("paged-buffer-test")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    emitter = EventEmitter(logger)
    for index in range(23):
        emitter.emit(EventType.CAPTURE_SUCCEEDED, capture_id=str(index))

    first, total = handler.page(1, 10)
    third, _total = handler.page(3, 10)

    assert total == 23
    assert [item["capture_id"] for item in first] == [
        str(index) for index in range(22, 12, -1)
    ]
    assert [item["capture_id"] for item in third] == ["2", "1", "0"]
    with pytest.raises(ValueError):
        handler.page(0, 10)
    with pytest.raises(ValueError):
        handler.page(1, 51)


@pytest.mark.covers("WEB-009")
def test_e4_preview_consumption_does_not_mutate_core_pipeline(
    tmp_path: Path,
) -> None:
    camera = PreviewCamera()
    node, _provider, _storages = web_runtime(tmp_path, camera)
    frame = next(camera.frames())

    result = node.collect_once()

    assert frame.media_type == "image/jpeg"
    assert result.accepted


@pytest.mark.covers("CAP-001", "WEB-001")
def test_web_can_request_one_immediate_capture(tmp_path: Path) -> None:
    camera = PreviewCamera()
    node, provider, storages = web_runtime(tmp_path, camera)
    app = create_app(
        node,
        provider,
        storages,
        manage_lifecycle=False,
    )

    response = endpoint(app, "/api/capture", "POST")()

    assert response["capture_id"]
    assert response["captured_at"]
    assert "terminal_state" not in response


def test_capture_returns_after_handoff_before_storage_completion(
    tmp_path: Path,
) -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingStorage(FakeStorage):
        def save(self, data: bytes, name: str):
            started.set()
            release.wait(1)
            return super().save(data, name)

    camera = PreviewCamera()
    configuration = make_configuration(tmp_path / "config.json")
    camera_provider = FakeCameraProvider(camera)
    storage = BlockingStorage("slow")
    storage_provider = FakeStorageProvider(storage)
    node = CaptureNode(camera_provider, configuration, storage_provider)
    app = create_app(
        node,
        camera_provider,
        storage_provider,
        manage_lifecycle=False,
    )

    response = endpoint(app, "/api/capture", "POST")()

    assert response["capture_id"]
    assert started.wait(0.5)
    assert node.is_pending
    with pytest.raises(ApiProblem) as raised:
        endpoint(app, "/api/capture", "POST")()
    assert raised.value.code == "capture_in_progress"

    release.set()
    wait_until(lambda: not node.is_pending)


def test_api_errors_use_problem_details(tmp_path: Path) -> None:
    camera = FakeCamera()
    node, provider, storages = web_runtime(tmp_path, camera)
    app = create_app(node, provider, storages, manage_lifecycle=False)

    with pytest.raises(ApiProblem) as raised:
        endpoint(app, "/api/preview")()
    response = problem_response(app, "/api/preview", raised.value)

    assert response.status_code == 501
    assert response.headers["content-type"].startswith("application/problem+json")
    assert json.loads(response.body) == {
        "type": "/problems/preview-not-supported",
        "title": "Preview not supported",
        "status": 501,
        "detail": "The configured camera does not provide preview frames.",
        "instance": "/api/preview",
        "code": "preview_not_supported",
    }


def test_web_browses_storage_through_provider_contract(tmp_path: Path) -> None:
    root = tmp_path / "captures"
    root.mkdir()
    (root / "capture.jpg").write_bytes(b"jpeg-data")
    camera = PreviewCamera()
    configuration = make_configuration(tmp_path / "config.json")
    camera_provider = FakeCameraProvider(camera)
    storage_provider = ConfiguredStorageProvider(
        (
            FileStorageOptions(
                type="file",
                name="local",
                directory=root,
            ),
        )
    )
    node = CaptureNode(camera_provider, configuration, storage_provider)
    app = create_app(
        node,
        camera_provider,
        storage_provider,
        manage_lifecycle=False,
    )

    targets = endpoint(app, "/api/storage/targets")()
    listing = endpoint(app, "/api/storage/files")(
        target="local",
        page=1,
        page_size=12,
    )
    download = endpoint(app, "/api/storage/files/{target}/{filename}")(
        target="local",
        filename="capture.jpg",
    )

    assert targets == {"items": [{"name": "local"}]}
    assert listing["items"][0]["name"] == "capture.jpg"
    assert download.body == b"jpeg-data"
    assert download.headers["content-type"] == "image/jpeg"


def test_web_reports_unsupported_storage_listing_as_problem(
    tmp_path: Path,
) -> None:
    camera = PreviewCamera()
    configuration = make_configuration(tmp_path / "config.json")
    camera_provider = FakeCameraProvider(camera)
    storage_provider = FakeStorageProvider(FakeStorage("write-only"))
    node = CaptureNode(camera_provider, configuration, storage_provider)
    app = create_app(
        node,
        camera_provider,
        storage_provider,
        manage_lifecycle=False,
    )

    with pytest.raises(ApiProblem) as raised:
        endpoint(app, "/api/storage/files")(
            target="write-only",
            page=1,
            page_size=12,
        )
    response = problem_response(app, "/api/storage/files", raised.value)

    assert response.status_code == 405
    assert json.loads(response.body)["code"] == "storage_operation_not_supported"


def test_web_lifespan_drains_accepted_storage_before_exit(
    tmp_path: Path,
) -> None:
    storage = FakeStorage("slow", delay=0.1)
    configuration = make_configuration(tmp_path / "config.json")
    camera_provider = FakeCameraProvider(PreviewCamera())
    storage_provider = FakeStorageProvider(storage)
    node = CaptureNode(camera_provider, configuration, storage_provider)
    app = create_app(node, camera_provider, storage_provider)

    async def run_lifespan() -> None:
        async with app.router.lifespan_context(app):
            result = node.collect_once()
            assert result.accepted
            assert node.is_pending

    asyncio.run(run_lifespan())

    assert not node.is_accepting_work
    assert not node.is_pending
    assert len(storage.calls) == 1
