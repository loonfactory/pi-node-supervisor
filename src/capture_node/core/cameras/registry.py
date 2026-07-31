"""Immutable camera registration and lookup."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Callable, Generic, TypeVar, cast

from capture_node.core.cameras.contracts import Camera, CameraDevice
from capture_node.core.cameras.options import (
    CameraOptions,
    Picamera2Options,
    V4L2CameraOptions,
)
from capture_node.core.models import StrictModel

OptionsT = TypeVar("OptionsT", bound=StrictModel)


@dataclass(frozen=True, slots=True)
class CameraRegistration(Generic[OptionsT]):
    """Binds one camera type to construction and optional discovery."""

    key: str
    options_type: type[OptionsT]
    factory: Callable[[OptionsT], Camera]
    discoverer: Callable[[], Sequence[CameraDevice]] | None = None

    def create(self, options: CameraOptions) -> Camera:
        if not isinstance(options, self.options_type):
            raise TypeError(
                f"{self.key} requires {self.options_type.__name__}, "
                f"got {type(options).__name__}"
            )
        return self.factory(cast(OptionsT, options))

    def devices(self) -> Sequence[CameraDevice]:
        if self.discoverer is None:
            return ()
        return self.discoverer()


class CameraRegistry:
    """Resolves Camera registrations by validated configuration key."""

    def __init__(self, registrations: Sequence[CameraRegistration]) -> None:
        indexed: dict[str, CameraRegistration] = {}
        for registration in registrations:
            if not registration.key:
                raise ValueError("camera registration key must not be empty")
            if registration.key in indexed:
                raise ValueError(
                    f"camera registration already exists: {registration.key}"
                )
            indexed[registration.key] = registration
        self._registrations = MappingProxyType(indexed)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self._registrations)

    def create(self, options: CameraOptions) -> Camera:
        try:
            registration = self._registrations[options.type]
        except KeyError as exc:
            raise ValueError(f"unknown camera type: {options.type}") from exc
        return registration.create(options)

    def devices(self) -> tuple[CameraDevice, ...]:
        devices: list[CameraDevice] = []
        for registration in self._registrations.values():
            try:
                devices.extend(registration.devices())
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "%s camera discovery failed: %s",
                    registration.key,
                    exc,
                )
        return tuple(devices)


@lru_cache(maxsize=1)
def default_camera_registry() -> CameraRegistry:
    """Return the immutable registry shipped with CaptureNode."""

    from capture_node.core.cameras.picamera2 import (
        Picamera2Camera,
        discover_picamera2_devices,
    )
    from capture_node.core.cameras.v4l2 import (
        V4L2Camera,
        discover_v4l2_camera_devices,
    )

    return CameraRegistry(
        (
            CameraRegistration(
                "picamera2",
                Picamera2Options,
                Picamera2Camera.from_options,
                discover_picamera2_devices,
            ),
            CameraRegistration(
                "v4l2",
                V4L2CameraOptions,
                V4L2Camera.from_options,
                discover_v4l2_camera_devices,
            ),
        )
    )
