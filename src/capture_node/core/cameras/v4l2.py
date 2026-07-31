"""Core USB/UVC camera adapter using FFmpeg's V4L2 input."""

from __future__ import annotations

import re
import subprocess
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from capture_node.core.cameras.contracts import (
    CameraControl,
    CameraDevice,
    CameraStatus,
    Capture,
    PreviewFrame,
)
from capture_node.core.cameras.options import V4L2CameraOptions
from capture_node.core.cameras.v4l2_stream import (
    FFmpegMjpegStream,
    extract_jpeg_frames,
)

_CONTROL_NAME = re.compile(r"^[A-Za-z0-9_]+$")
_CONTROL_LINE = re.compile(
    r"^\s*([A-Za-z0-9_]+)\s+0x[0-9A-Fa-f]+\s+\(([^)]+)\)\s*:\s*(.*)$"
)
_MENU_LINE = re.compile(r"^\s+(-?\d+):\s+(.+)$")
_NUMERIC_ATTRIBUTE = re.compile(r"\b(min|max|step|default|value)=(-?\d+(?:\.\d+)?)")

__all__ = [
    "V4L2Camera",
    "V4L2Device",
    "discover_v4l2_camera_devices",
    "discover_v4l2_devices",
    "extract_jpeg_frames",
    "parse_v4l2_controls",
]


@dataclass(frozen=True, slots=True)
class V4L2Device:
    path: Path
    name: str


class V4L2Camera:
    """Shares one continuous MJPEG source between stills and Preview."""

    @classmethod
    def from_options(cls, options: V4L2CameraOptions) -> V4L2Camera:
        return cls(
            device=options.device,
            width=options.width,
            height=options.height,
            frame_rate=options.frame_rate,
            startup_timeout_seconds=options.startup_timeout_seconds,
        )

    def __init__(
        self,
        *,
        device: Path = Path("/dev/video0"),
        width: int = 1280,
        height: int = 720,
        frame_rate: float = 15,
        startup_timeout_seconds: float = 10,
        process_factory: Any = subprocess.Popen,
        control_runner: Any = subprocess.run,
    ) -> None:
        if width <= 0 or height <= 0 or frame_rate <= 0:
            raise ValueError("width, height, and frame_rate must be positive")
        if startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be positive")
        if not device.exists():
            raise ValueError(f"V4L2 device does not exist: {device}")

        self._device = device
        self._width = width
        self._height = height
        self._frame_rate = frame_rate
        self._startup_timeout = startup_timeout_seconds
        self._control_runner = control_runner
        self._parameters_lock = threading.Lock()
        self._parameters: dict[str, Any] = {}
        self._stream = FFmpegMjpegStream(
            device=device,
            width=width,
            height=height,
            frame_rate=frame_rate,
            startup_timeout_seconds=startup_timeout_seconds,
            process_factory=process_factory,
        )

    def capture(self) -> Capture:
        data, _ = self._stream.current_frame()
        captured_at = datetime.now(UTC)
        snapshot_at = datetime.now(UTC)
        try:
            observed_controls = {
                control.name: control.value for control in self.controls()
            }
            snapshot: dict[str, Any] = {
                "control_snapshot_at": snapshot_at.isoformat(),
                "observed_controls": observed_controls,
            }
        except Exception as exc:
            snapshot = {
                "control_snapshot_at": snapshot_at.isoformat(),
                "control_snapshot_error": str(exc) or type(exc).__name__,
            }
        with Image.open(BytesIO(data)) as decoded:
            image = decoded.convert("RGB")
            image.load()
        with self._parameters_lock:
            configured_parameters = dict(self._parameters)
        return Capture(
            image=image,
            captured_at=captured_at,
            parameters={
                "device": str(self._device),
                "width": self._width,
                "height": self._height,
                "frame_rate": self._frame_rate,
                **configured_parameters,
                **snapshot,
            },
        )

    def frames(self) -> Iterator[PreviewFrame]:
        sequence = -1
        while True:
            data, sequence = self._stream.frame_after(sequence)
            yield PreviewFrame(data)

    def apply(self, parameters: Mapping[str, Any]) -> None:
        if not parameters:
            return
        controls = ",".join(
            f"{self._validate_control_name(name)}={self._control_value(value)}"
            for name, value in parameters.items()
        )
        completed = self._control_runner(
            [
                "v4l2-ctl",
                "--device",
                str(self._device),
                f"--set-ctrl={controls}",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=self._startup_timeout,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"cannot apply V4L2 controls: {detail}")
        with self._parameters_lock:
            self._parameters = dict(parameters)

    def controls(self) -> tuple[CameraControl, ...]:
        completed = self._control_runner(
            [
                "v4l2-ctl",
                "--device",
                str(self._device),
                "--list-ctrls-menus",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=self._startup_timeout,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"cannot read V4L2 controls: {detail}")
        return parse_v4l2_controls(completed.stdout)

    def status(self) -> CameraStatus:
        if self._stream.closed:
            return CameraStatus(False, "camera is closed")
        if not self._device.exists():
            return CameraStatus(False, f"V4L2 device is disconnected: {self._device}")
        if failure := self._stream.failure:
            return CameraStatus(False, str(failure))
        if not self._stream.ready:
            return CameraStatus(False, "camera is starting")
        return CameraStatus(True)

    def close(self) -> None:
        self._stream.close()

    @staticmethod
    def _validate_control_name(name: str) -> str:
        if not _CONTROL_NAME.fullmatch(name):
            raise ValueError(f"invalid V4L2 control name: {name!r}")
        return name

    @staticmethod
    def _control_value(value: Any) -> str:
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        raise ValueError("V4L2 control values must be boolean or numeric")


def parse_v4l2_controls(output: str) -> tuple[CameraControl, ...]:
    """Parse v4l2-ctl's stable human-readable control listing."""

    controls: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in output.splitlines():
        control_match = _CONTROL_LINE.match(line)
        if control_match:
            name, control_type, attributes_text = control_match.groups()
            attributes = {
                key: _number(value)
                for key, value in _NUMERIC_ATTRIBUTE.findall(attributes_text)
            }
            flags = (
                attributes_text.split("flags=", 1)[1].lower()
                if "flags=" in attributes_text
                else ""
            )
            value = attributes.get("value", 0)
            default = attributes.get("default")
            if control_type == "bool":
                value = bool(value)
                default = bool(default) if default is not None else None
            current = {
                "name": name,
                "label": name.replace("_", " ").title(),
                "control_type": control_type,
                "value": value,
                "minimum": attributes.get("min"),
                "maximum": attributes.get("max"),
                "step": attributes.get("step"),
                "default": default,
                "options": {},
                "inactive": "inactive" in flags or "read-only" in flags,
            }
            controls.append(current)
            continue
        menu_match = _MENU_LINE.match(line)
        if menu_match and current is not None:
            option_value, label = menu_match.groups()
            current["options"][int(option_value)] = label
    return tuple(CameraControl(**control) for control in controls)


def discover_v4l2_devices(
    *,
    sys_class: Path = Path("/sys/class/video4linux"),
    runner: Any = subprocess.run,
) -> tuple[V4L2Device, ...]:
    """Return only V4L2 nodes that accept the video-capture format ioctl."""

    devices: list[V4L2Device] = []
    if not sys_class.is_dir():
        return ()
    for entry in sorted(sys_class.glob("video*"), key=lambda path: path.name):
        driver_link = entry / "device" / "driver"
        if driver_link.exists():
            try:
                if driver_link.resolve().name != "uvcvideo":
                    continue
            except OSError:
                continue
        device = Path("/dev") / entry.name
        completed = runner(
            ["v4l2-ctl", "--device", str(device), "--get-fmt-video"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if completed.returncode != 0:
            continue
        try:
            name = (entry / "name").read_text(encoding="utf-8").strip()
        except OSError:
            name = entry.name
        devices.append(V4L2Device(device, name))
    return tuple(devices)


def discover_v4l2_camera_devices() -> tuple[CameraDevice, ...]:
    """Return every supported UVC capture node as a selectable camera."""

    return tuple(
        CameraDevice(
            identifier=f"v4l2:{device.path}",
            name=device.name,
            options=V4L2CameraOptions(type="v4l2", device=device.path),
        )
        for device in discover_v4l2_devices()
    )


def _number(value: str) -> int | float:
    parsed = float(value)
    return int(parsed) if parsed.is_integer() else parsed
