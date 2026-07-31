"""Camera contracts and configured implementations."""

from capture_node.core.cameras.contracts import (
    Camera,
    CameraControl,
    CameraControlSource,
    CameraDevice,
    CameraDiscoveryProvider,
    CameraProvider,
    CameraReplacementProvider,
    CameraStatus,
    CameraStatusSource,
    Capture,
    FrameSource,
    PreviewFrame,
)
from capture_node.core.cameras.options import (
    CameraOptions,
    Picamera2Options,
    V4L2CameraOptions,
)
from capture_node.core.cameras.provider import ConfiguredCameraProvider
from capture_node.core.cameras.registry import (
    CameraRegistration,
    CameraRegistry,
    default_camera_registry,
)

__all__ = [
    "Camera",
    "CameraControl",
    "CameraControlSource",
    "CameraDevice",
    "CameraDiscoveryProvider",
    "CameraOptions",
    "CameraProvider",
    "CameraRegistration",
    "CameraRegistry",
    "CameraReplacementProvider",
    "CameraStatus",
    "CameraStatusSource",
    "Capture",
    "ConfiguredCameraProvider",
    "FrameSource",
    "Picamera2Options",
    "PreviewFrame",
    "V4L2CameraOptions",
    "default_camera_registry",
]
