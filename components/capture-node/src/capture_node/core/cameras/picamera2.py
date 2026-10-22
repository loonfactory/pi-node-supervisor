"""Raspberry Pi CSI camera adapter owned by Core."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from PIL import Image

from capture_node.core.cameras.contracts import (
    CameraControl,
    CameraDevice,
    CameraStatus,
    Capture,
    PreviewFormat,
    PreviewFrame,
    RawCapture,
)
from capture_node.core.cameras.options import CameraSynchronizationOptions, Picamera2Options
from capture_node.core.cameras.preview_encoding import encode_preview
from capture_node.core.cameras.synchronized_frames import SynchronizedFrames

_HORIZONTAL_FLIP = "horizontal_flip"
_VERTICAL_FLIP = "vertical_flip"


class Picamera2Camera:
    """Still and preview capabilities backed by one Picamera2 main stream."""

    @classmethod
    def from_options(cls, options: Picamera2Options) -> Picamera2Camera:
        return cls(
            camera_num=options.camera_num,
            size=options.size,
            preview_fps=options.preview_fps,
            preview_quality=options.preview_quality,
            synchronization=options.synchronization,
        )

    def __init__(
        self,
        *,
        camera_num: int = 0,
        size: tuple[int, int] | None = None,
        preview_fps: float = 10,
        preview_quality: int = 75,
        synchronization: CameraSynchronizationOptions | None = None,
    ) -> None:
        if camera_num < 0 or preview_fps <= 0:
            raise ValueError(
                "camera_num must not be negative and preview_fps must be positive"
            )
        try:
            from libcamera import Transform
            from picamera2 import Picamera2
        except ImportError as exc:
            raise RuntimeError(
                "Picamera2 is required for the Raspberry Pi camera adapter; "
                "install capture-node[raspberry-pi]"
            ) from exc

        self._camera = Picamera2(camera_num)
        self._sensor_resolution = tuple(self._camera.sensor_resolution)
        self._sensor_modes = tuple(dict(mode) for mode in self._camera.sensor_modes)
        self._transform_type = Transform
        self._size = size or self._sensor_resolution
        self._horizontal_flip = False
        self._vertical_flip = False
        self._preview_period = 1 / preview_fps
        self._preview_quality = preview_quality
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._synchronization = synchronization
        self._synchronized_frames = SynchronizedFrames() if synchronization else None
        try:
            if self._synchronized_frames is not None:
                self._camera.post_callback = self._synchronized_frames.observe
            self._camera.configure(self._configuration(False, False))
            self._camera.start()
        except BaseException:
            self._camera.close()
            raise

    def capture(self, *, include_raw: bool = False) -> Capture:
        if self._synchronization is not None:
            return self._capture_synchronized(0, include_raw=include_raw)
        if include_raw:
            return self._capture_with_raw()
        with self._lock:
            request = self._camera.capture_request()
            try:
                array = request.make_array("main")
                metadata = request.get_metadata()
            finally:
                request.release()
        return Capture(
            Image.fromarray(array),
            datetime.now(UTC),
            dict(metadata),
        )

    def capture_at(self, target_ns: int, *, include_raw: bool = False) -> Capture:
        if self._synchronization is None:
            raise ValueError("captureAtNs requires camera synchronization")
        now = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        if not now < target_ns < now + self._synchronization.timeout_seconds * 1e9:
            raise ValueError("captureAtNs must be in the future and within the capture timeout")
        return self._capture_synchronized(target_ns, include_raw=include_raw)

    def _capture_synchronized(self, target_ns: int, *, include_raw: bool) -> Capture:
        assert self._synchronization is not None
        assert self._synchronized_frames is not None
        with self._lock:
            request = self._synchronized_frames.capture(
                target_ns, self._synchronization.timeout_seconds
            )
            try:
                metadata = dict(request.get_metadata())
                timestamp = metadata.get("SensorTimestamp")
                if not isinstance(timestamp, int) or timestamp <= 0:
                    raise RuntimeError("synchronized frame has no SensorTimestamp")
                if target_ns and timestamp - target_ns > 1e9 / self._synchronization.frame_rate:
                    raise TimeoutError("synchronized capture missed its target frame window")
                array = request.make_array("main")
                raw = None
                if include_raw:
                    output = BytesIO()
                    request.save_dng(output, "raw")
                    raw = RawCapture(output.getvalue())
            finally:
                request.release()
        return Capture(Image.fromarray(array), datetime.now(UTC), metadata, raw)

    def _capture_with_raw(self) -> Capture:
        with self._lock:
            preview_configuration = self._camera.camera_config
            still_configuration = self._still_configuration()
            request = None
            try:
                request = self._camera.switch_mode_capture_request_and_stop(
                    still_configuration
                )
                array = request.make_array("main")
                metadata = request.get_metadata()
                output = BytesIO()
                request.save_dng(output, "raw")
            except BaseException:
                if request is not None:
                    request.release()
                self._restore_preview(preview_configuration, stop_first=True)
                raise
            try:
                request.release()
            finally:
                self._restore_preview(preview_configuration, stop_first=True)
        return Capture(
            Image.fromarray(array),
            datetime.now(UTC),
            dict(metadata),
            RawCapture(output.getvalue()),
        )

    def apply(self, parameters: Mapping[str, Any]) -> None:
        controls = dict(parameters)
        if self._synchronization is not None:
            reserved = {"FrameRate", "FrameDurationLimits", "SyncMode", "SyncFrames"}
            if reserved.intersection(controls):
                raise ValueError("synchronization owns frame timing controls")
        horizontal_flip = self._flip_value(
            controls.pop(_HORIZONTAL_FLIP, self._horizontal_flip),
            _HORIZONTAL_FLIP,
        )
        vertical_flip = self._flip_value(
            controls.pop(_VERTICAL_FLIP, self._vertical_flip),
            _VERTICAL_FLIP,
        )
        with self._lock:
            if (
                horizontal_flip != self._horizontal_flip
                or vertical_flip != self._vertical_flip
            ):
                self._reconfigure(horizontal_flip, vertical_flip)
            if controls:
                self._camera.set_controls(controls)

    def controls(self) -> tuple[CameraControl, ...]:
        with self._lock:
            return (
                CameraControl(
                    name=_HORIZONTAL_FLIP,
                    label="Horizontal flip",
                    control_type="bool",
                    value=self._horizontal_flip,
                    default=False,
                ),
                CameraControl(
                    name=_VERTICAL_FLIP,
                    label="Vertical flip",
                    control_type="bool",
                    value=self._vertical_flip,
                    default=False,
                ),
            )

    def frames(
        self, *, format: PreviewFormat = PreviewFormat.PNG
    ) -> Iterator[PreviewFrame]:
        while not self._closed.is_set():
            started_at = time.monotonic()
            with self._lock:
                array = self._camera.capture_array("main")
            yield encode_preview(
                Image.fromarray(array), format, jpeg_quality=self._preview_quality
            )
            elapsed = time.monotonic() - started_at
            self._closed.wait(max(0, self._preview_period - elapsed))

    def status(self) -> CameraStatus:
        if self._closed.is_set():
            return CameraStatus(False, "camera is closed")
        return CameraStatus(True)

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        if self._synchronized_frames is not None:
            self._synchronized_frames.close()
        with self._lock:
            self._camera.stop()
            self._camera.close()

    def _configuration(self, horizontal_flip: bool, vertical_flip: bool):
        # Picamera2's BGR888 format is exposed to Python as RGB byte order.
        main: dict[str, Any] = {"format": "BGR888"}
        if self._size is not None:
            main["size"] = self._size
        if self._synchronization is not None:
            from libcamera import controls

            role = self._synchronization.role
            mode = (
                controls.rpi.SyncModeEnum.Server
                if role == "server" else controls.rpi.SyncModeEnum.Client
            )
            return self._camera.create_video_configuration(
                main=main,
                raw={},
                sensor=self._sensor_configuration(),
                buffer_count=3,
                queue=False,
                controls={"FrameRate": self._synchronization.frame_rate, "SyncMode": mode},
                transform=self._transform_type(hflip=horizontal_flip, vflip=vertical_flip),
            )
        return self._camera.create_preview_configuration(
            main=main,
            transform=self._transform_type(
                hflip=horizontal_flip,
                vflip=vertical_flip,
            ),
        )

    def _still_configuration(self):
        main: dict[str, Any] = {"format": "BGR888"}
        if self._size is not None:
            main["size"] = self._size
        return self._camera.create_still_configuration(
            main=main,
            raw={},
            sensor=self._sensor_configuration(),
            transform=self._transform_type(
                hflip=self._horizontal_flip,
                vflip=self._vertical_flip,
            ),
        )

    def _sensor_configuration(self) -> dict[str, Any]:
        sensor: dict[str, Any] = {
            "output_size": self._sensor_resolution,
        }
        matching_depths = [
            int(mode["bit_depth"])
            for mode in self._sensor_modes
            if tuple(mode.get("size", ())) == sensor["output_size"]
            and mode.get("bit_depth") is not None
        ]
        if matching_depths:
            sensor["bit_depth"] = max(matching_depths)
        return sensor

    def _restore_preview(self, configuration, *, stop_first: bool = False) -> None:
        if stop_first:
            try:
                self._camera.stop()
            except RuntimeError:
                pass
        self._camera.configure(configuration)
        self._camera.start()

    def _reconfigure(self, horizontal_flip: bool, vertical_flip: bool) -> None:
        previous = (self._horizontal_flip, self._vertical_flip)
        self._camera.stop()
        try:
            self._camera.configure(
                self._configuration(horizontal_flip, vertical_flip)
            )
            self._camera.start()
        except BaseException:
            self._camera.configure(self._configuration(*previous))
            self._camera.start()
            raise
        self._horizontal_flip = horizontal_flip
        self._vertical_flip = vertical_flip

    @staticmethod
    def _flip_value(value: Any, name: str) -> bool:
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be boolean")
        return value


def discover_picamera2_devices() -> tuple[CameraDevice, ...]:
    """Return every CSI camera reported by Picamera2."""

    try:
        from picamera2 import Picamera2
    except ImportError:
        return ()

    devices: list[CameraDevice] = []
    for index, information in enumerate(Picamera2.global_camera_info()):
        if "/usb@" in str(information.get("Id", "")).lower():
            continue
        camera_num = int(information.get("Num", index))
        model = str(information.get("Model") or f"Camera {camera_num}")
        devices.append(
            CameraDevice(
                identifier=f"picamera2:{camera_num}",
                name=model,
                options=Picamera2Options(cameraNum=camera_num),
            )
        )
    return tuple(devices)
