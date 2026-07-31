"""Camera contracts and optional capabilities."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from PIL import Image

if TYPE_CHECKING:
    from capture_node.core.cameras.options import CameraOptions


@dataclass(frozen=True, slots=True)
class Capture:
    """A decoded still image and metadata observed at capture time."""

    image: Image.Image
    captured_at: datetime
    parameters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PreviewFrame:
    """A browser-ready preview frame."""

    data: bytes
    media_type: str = "image/jpeg"


@dataclass(frozen=True, slots=True)
class CameraControl:
    """One discoverable camera control and its current device value."""

    name: str
    label: str
    control_type: str
    value: int | float | bool
    minimum: int | float | None = None
    maximum: int | float | None = None
    step: int | float | None = None
    default: int | float | bool | None = None
    options: Mapping[int, str] = field(default_factory=dict)
    inactive: bool = False


@dataclass(frozen=True, slots=True)
class CameraStatus:
    """Current availability of a camera implementation."""

    connected: bool
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class CameraDevice:
    """One camera device available for provider selection."""

    identifier: str
    name: str
    options: CameraOptions


class Camera(Protocol):
    """Still-capture capability."""

    def capture(self) -> Capture:
        """Capture and return one decoded still image."""

    def apply(self, parameters: Mapping[str, Any]) -> None:
        """Apply the camera implementation's supported parameters."""


class CameraProvider(Protocol):
    """Provides the active camera and owns its lifecycle."""

    def camera(self) -> Camera:
        """Return the currently active camera boundary."""

    def close(self) -> None:
        """Release the active camera and provider resources."""


@runtime_checkable
class CameraDiscoveryProvider(Protocol):
    """Optional camera-device discovery capability."""

    def devices(self) -> Sequence[CameraDevice]:
        """Return selectable camera devices."""


@runtime_checkable
class CameraReplacementProvider(Protocol):
    """Optional live camera replacement capability."""

    def replace(
        self,
        options: CameraOptions,
        parameters: Mapping[str, Any],
    ) -> None:
        """Replace the active camera and preserve the previous configuration."""


@runtime_checkable
class FrameSource(Protocol):
    """Optional preview capability, separate from still capture."""

    def frames(self) -> Iterator[PreviewFrame]:
        """Yield frames from the same source used for still capture."""


@runtime_checkable
class CameraControlSource(Protocol):
    """Optional camera-control discovery capability."""

    def controls(self) -> Sequence[CameraControl]:
        """Return controls with live values and supported ranges."""


@runtime_checkable
class CameraStatusSource(Protocol):
    """Optional live camera availability capability."""

    def status(self) -> CameraStatus:
        """Return the current camera connection state."""
