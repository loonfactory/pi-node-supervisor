"""FastAPI host and HTTP contracts for CaptureNode Web."""

from __future__ import annotations

import argparse
import base64
import io
import logging
import mimetypes
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError, field_validator
from pydantic.alias_generators import to_camel

from capture_node.core import (
    CaptureBundle,
    CaptureFailed,
    CaptureInProgress,
    CaptureNode,
    EncodingFailed,
    InvalidCaptureParameters,
)
from capture_node.core.cameras import (
    Camera,
    CameraControlSource,
    CameraControlsUnavailable,
    ConfiguredCameraProvider,
    CameraDiscoveryProvider,
    CameraProvider,
    CameraReplacementProvider,
    CameraStatus,
    CameraStatusSource,
    FrameSource,
    PreviewFrame,
)
from capture_node.core.config import Configuration, UnsupportedRuntimeChange
from capture_node.core.encodings.options import ImageFormatsOptions
from capture_node.core.events import LOGGER_NAME
from capture_node.core.models import StrictModel
from capture_node.core.storages import ConfiguredStorageProvider, StorageProvider
from capture_node.web.event_buffer import EventBuffer
from capture_node.web.logging_config import configure_logging
from capture_node.web.problems import ApiProblem
from capture_node.web.storage_browser import StorageBrowser

_MEDIA_TYPES = {".dng": "image/x-adobe-dng"}
_CAPTURE_BOUNDARY = "capture"
_CAPTURE_STREAM_CHUNK_SIZE = 1024 * 1024


class CaptureRequest(StrictModel):
    """Supervisor-owned request for one streamed capture bundle."""

    request_id: str = Field(min_length=1, max_length=128)
    parameters: dict[str, Any] = Field(default_factory=dict)
    formats: ImageFormatsOptions | None = None

    @field_validator("request_id")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if not value.isascii() or any(
            ord(character) <= 0x20 or ord(character) == 0x7F
            for character in value
        ):
            raise ValueError(
                "request_id must contain visible ASCII characters without spaces"
            )
        return value


def create_app(
    node: CaptureNode,
    camera_provider: CameraProvider,
    storage_provider: StorageProvider,
    *,
    static_directory: Path | None = None,
    manage_lifecycle: bool = True,
):
    """Create the Web host without coupling Core to FastAPI."""

    try:
        from fastapi import FastAPI, Request
        from fastapi.exceptions import RequestValidationError
        from fastapi.responses import (
            FileResponse,
            JSONResponse,
            Response,
            StreamingResponse,
        )
        from fastapi.staticfiles import StaticFiles
        from starlette.exceptions import HTTPException as StarletteHTTPException
    except ImportError as exc:
        raise RuntimeError(
            "FastAPI is required for CaptureNode Web; install capture-node[web]"
        ) from exc

    event_buffer = EventBuffer()
    core_logger = logging.getLogger(LOGGER_NAME)
    core_logger.addHandler(event_buffer)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if manage_lifecycle:
            node.start()
        try:
            yield
        finally:
            if manage_lifecycle:
                node.stop()
            core_logger.removeHandler(event_buffer)

    app = FastAPI(title="CaptureNode", version="0.1.0", lifespan=lifespan)
    app.state.capture_node = node
    app.state.camera_provider = camera_provider
    app.state.storage_provider = storage_provider
    app.state.event_buffer = event_buffer

    @app.exception_handler(ApiProblem)
    async def api_problem(request: Request, exc: ApiProblem) -> JSONResponse:
        return JSONResponse(
            exc.document(request.url.path),
            status_code=exc.status,
            media_type="application/problem+json",
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_problem(
        request: Request,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        if request.url.path == "/api/captures":
            problem = ApiProblem(
                422,
                "invalid_capture_parameters",
                "Invalid capture parameters",
                "The capture request is invalid.",
            )
            return JSONResponse(
                problem.document(request.url.path),
                status_code=problem.status,
                media_type="application/problem+json",
            )
        problem = ApiProblem(
            422,
            "validation_failed",
            "Validation failed",
            "The request is invalid.",
        )
        return JSONResponse(
            problem.document(request.url.path),
            status_code=problem.status,
            media_type="application/problem+json",
        )

    @app.exception_handler(StarletteHTTPException)
    async def framework_problem(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "The request failed."
        problem = ApiProblem(
            exc.status_code,
            "http_error",
            _http_title(exc.status_code),
            detail,
        )
        return JSONResponse(
            problem.document(request.url.path),
            status_code=problem.status,
            media_type="application/problem+json",
            headers=exc.headers,
        )

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return node.options.model_dump(mode="json")

    @app.put("/api/config")
    async def update_config(document: dict[str, Any]) -> dict[str, Any]:
        try:
            result = node.update_options(document)
        except (ValidationError, UnsupportedRuntimeChange) as exc:
            raise ApiProblem(
                422,
                "validation_failed",
                "Validation failed",
                str(exc),
            ) from exc
        if not result.succeeded:
            assert result.cause is not None
            raise ApiProblem(
                409,
                "configuration_update_failed",
                "Configuration update failed",
                str(result.cause),
            )
        return result.options.model_dump(mode="json")

    @app.get("/api/events")
    def get_events(
        page: int = 1,
        pageSize: int = 10,
    ) -> dict[str, Any]:
        try:
            items, total = event_buffer.page(page, pageSize)
        except ValueError as exc:
            raise ApiProblem(
                422,
                "validation_failed",
                "Validation failed",
                str(exc),
            ) from exc
        pages = max(1, (total + pageSize - 1) // pageSize)
        return {
            "items": items,
            "page": page,
            "pageSize": pageSize,
            "total": total,
            "pages": pages,
            "latestCaptureId": event_buffer.latest_capture_id(),
        }

    @app.get("/api/camera/devices")
    def get_camera_devices() -> dict[str, Any]:
        if not isinstance(camera_provider, CameraDiscoveryProvider):
            return {"items": []}
        return {
            "items": [
                {
                    "id": device.identifier,
                    "type": device.options.type,
                    "name": device.name,
                    "options": device.options.model_dump(mode="json"),
                }
                for device in camera_provider.devices()
            ]
        }

    @app.get("/api/camera/controls")
    def get_camera_controls() -> dict[str, Any]:
        camera = camera_provider.camera()
        if not isinstance(camera, CameraControlSource):
            return {"items": []}
        try:
            controls = camera.controls()
        except CameraControlsUnavailable as exc:
            return {
                "items": [_dataclass_document(control) for control in exc.controls],
                "available": bool(exc.controls),
                "warning": {
                    "code": exc.code,
                    "message": str(exc),
                },
            }
        except RuntimeError as exc:
            raise ApiProblem(
                409,
                "camera_controls_unavailable",
                "Camera controls unavailable",
                str(exc),
            ) from exc
        return {
            "items": [_dataclass_document(control) for control in controls],
            "available": True,
            "warning": None,
        }

    @app.put("/api/camera")
    async def update_camera(document: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(camera_provider, CameraReplacementProvider):
            raise ApiProblem(
                409,
                "camera_replacement_not_supported",
                "Camera replacement not supported",
                "The active host does not support camera replacement.",
            )
        try:
            result = node.update_camera_options(
                document,
                lambda candidate: camera_provider.replace(
                    candidate,
                    candidate.params,
                ),
            )
        except ValidationError as exc:
            raise ApiProblem(
                422,
                "validation_failed",
                "Validation failed",
                str(exc),
            ) from exc
        if not result.succeeded:
            assert result.cause is not None
            raise ApiProblem(
                409,
                "camera_replacement_failed",
                "Camera replacement failed",
                str(result.cause),
            )
        return result.options.camera.model_dump(mode="json")

    @app.get("/api/storage/targets")
    def get_storage_targets() -> dict[str, Any]:
        browser = StorageBrowser(storage_provider)
        return {
            "items": [{"name": target.name} for target in browser.targets]
        }

    @app.get("/api/storage/files")
    def get_storage_files(
        target: str,
        page: int = 1,
        pageSize: int = 12,
    ) -> dict[str, Any]:
        browser = StorageBrowser(storage_provider)
        try:
            result = browser.list_files(target, page, pageSize)
        except KeyError as exc:
            raise ApiProblem(
                404,
                "storage_not_found",
                "Storage not found",
                str(exc.args[0]),
            ) from exc
        except io.UnsupportedOperation as exc:
            raise ApiProblem(
                405,
                "storage_operation_not_supported",
                "Storage operation not supported",
                str(exc),
            ) from exc
        except ValueError as exc:
            raise ApiProblem(
                422,
                "validation_failed",
                "Validation failed",
                str(exc),
            ) from exc
        except OSError as exc:
            raise ApiProblem(
                503,
                "storage_unavailable",
                "Storage unavailable",
                str(exc),
            ) from exc
        return {
            "items": [_dataclass_document(item) for item in result.items],
            "page": page,
            "pageSize": pageSize,
            "total": result.total,
            "pages": max(1, (result.total + pageSize - 1) // pageSize),
        }

    @app.get("/api/storage/files/{target}/{filename}")
    def get_storage_file(target: str, filename: str):
        browser = StorageBrowser(storage_provider)
        try:
            data = browser.read_file(target, filename)
        except KeyError as exc:
            raise ApiProblem(
                404,
                "storage_not_found",
                "Storage not found",
                str(exc.args[0]),
            ) from exc
        except io.UnsupportedOperation as exc:
            raise ApiProblem(
                405,
                "storage_operation_not_supported",
                "Storage operation not supported",
                str(exc),
            ) from exc
        except (OSError, ValueError) as exc:
            raise ApiProblem(
                404,
                "stored_file_not_found",
                "Stored file not found",
                str(exc),
            ) from exc
        media_type = (
            _MEDIA_TYPES.get(Path(filename).suffix.lower())
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )
        return Response(data, media_type=media_type)

    @app.get("/api/preview")
    def preview():
        camera = camera_provider.camera()
        camera_status = _camera_status(camera)
        if not camera_status.connected:
            raise ApiProblem(
                503,
                "camera_unavailable",
                "Camera unavailable",
                camera_status.detail or "The camera is unavailable.",
            )
        if not isinstance(camera, FrameSource):
            raise ApiProblem(
                501,
                "preview_not_supported",
                "Preview not supported",
                "The configured camera does not provide preview frames.",
            )
        return StreamingResponse(
            _multipart_frames(camera.frames()),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.post("/api/capture")
    def capture_now() -> dict[str, Any]:
        camera = camera_provider.camera()
        camera_status = _camera_status(camera)
        if not camera_status.connected:
            raise ApiProblem(
                409,
                "camera_unavailable",
                "Camera unavailable",
                camera_status.detail or "The camera is unavailable.",
            )
        result = node.collect_once()
        if not result.accepted:
            raise ApiProblem(
                409,
                "capture_in_progress",
                "Capture in progress",
                "A collection is already in progress.",
            )
        if result.error is not None:
            raise ApiProblem(
                500,
                "capture_failed",
                "Capture failed",
                str(result.error) or type(result.error).__name__,
            )
        assert result.capture_id is not None
        assert result.captured_at is not None
        return {
            "captureId": result.capture_id,
            "capturedAt": result.captured_at.isoformat(),
        }

    @app.post("/api/captures")
    def capture_bundle(request: CaptureRequest):
        camera = camera_provider.camera()
        camera_status = _camera_status(camera)
        if not camera_status.connected:
            raise ApiProblem(
                409,
                "camera_unavailable",
                "Camera unavailable",
                camera_status.detail or "The camera is unavailable.",
            )
        try:
            bundle = node.capture_bundle(
                request.request_id,
                request.parameters,
                request.formats,
            )
        except CaptureInProgress as exc:
            raise ApiProblem(
                409,
                "capture_in_progress",
                "Capture in progress",
                str(exc),
            ) from exc
        except InvalidCaptureParameters as exc:
            raise ApiProblem(
                422,
                "invalid_capture_parameters",
                "Invalid capture parameters",
                str(exc),
            ) from exc
        except CaptureFailed as exc:
            raise ApiProblem(
                500,
                "capture_failed",
                "Capture failed",
                str(exc),
            ) from exc
        except EncodingFailed as exc:
            raise ApiProblem(
                500,
                "encoding_failed",
                "Encoding failed",
                str(exc),
            ) from exc
        try:
            return StreamingResponse(
                _capture_multipart(bundle, _CAPTURE_BOUNDARY),
                media_type=f"multipart/mixed; boundary={_CAPTURE_BOUNDARY}",
                headers={
                    "X-Request-Id": bundle.request_id,
                    "X-Capture-Id": bundle.capture_id,
                    "X-Captured-At": bundle.captured_at.isoformat(),
                    "X-Artifact-Count": str(len(bundle.artifacts)),
                },
            )
        except BaseException:
            bundle.close()
            raise

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        camera_status = _camera_status(camera_provider.camera())
        return {
            "status": "running",
            "cameraConnected": camera_status.connected,
            "cameraDetail": camera_status.detail,
        }

    web_dist = static_directory or Path(__file__).with_name("static")
    if web_dist.is_dir():
        assets = web_dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            requested = (web_dist / path).resolve()
            if (
                path
                and requested.is_file()
                and web_dist.resolve() in requested.parents
            ):
                return FileResponse(requested)
            return FileResponse(web_dist / "index.html")

    return app


def _multipart_frames(frames: Iterator[PreviewFrame]) -> Iterator[bytes]:
    for frame in frames:
        yield (
            b"--frame\r\n"
            + f"Content-Type: {frame.media_type}\r\n".encode("ascii")
            + f"Content-Length: {len(frame.data)}\r\n\r\n".encode("ascii")
            + frame.data
            + b"\r\n"
        )


async def _capture_multipart(
    bundle: CaptureBundle,
    boundary: str,
):
    try:
        for artifact in bundle.artifacts:
            digest = base64.b64encode(artifact.digest).decode("ascii")
            yield (
                f"--{boundary}\r\n"
                f"Content-Type: {artifact.media_type}\r\n"
                "Content-Disposition: attachment; "
                f'filename="{artifact.name}"\r\n'
                f"Content-Length: {len(artifact.data)}\r\n"
                f"Digest: sha-256={digest}\r\n"
                f"X-Capture-Id: {bundle.capture_id}\r\n\r\n"
            ).encode("ascii")
            data = memoryview(artifact.data)
            for offset in range(0, len(data), _CAPTURE_STREAM_CHUNK_SIZE):
                yield data[offset : offset + _CAPTURE_STREAM_CHUNK_SIZE]
            yield b"\r\n"
        yield f"--{boundary}--\r\n".encode("ascii")
    finally:
        bundle.close()


def _camera_status(camera: Camera) -> CameraStatus:
    if isinstance(camera, CameraStatusSource):
        return camera.status()
    return CameraStatus(True)


def _dataclass_document(value) -> dict[str, Any]:
    return {to_camel(name): item for name, item in asdict(value).items()}


def _http_title(status: int) -> str:
    return {
        404: "Not found",
        405: "Method not allowed",
        422: "Validation failed",
        500: "Internal server error",
    }.get(status, "Request failed")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run CaptureNode Web")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = create_parser().parse_args(argv)
    configure_logging(arguments.log_level)
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "Uvicorn is required for CaptureNode Web; install capture-node[web]"
        ) from exc

    configuration = Configuration.load(arguments.config)
    camera_provider = ConfiguredCameraProvider(
        configuration.current.camera,
        tolerate_unavailable=True,
    )
    storage_provider = ConfiguredStorageProvider(configuration.current.storages)
    node = CaptureNode(
        camera_provider,
        configuration,
        storage_provider,
    )
    app = create_app(node, camera_provider, storage_provider)
    try:
        uvicorn.run(app, host=arguments.host, port=arguments.port)
        return 0
    finally:
        camera_provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
