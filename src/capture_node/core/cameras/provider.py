"""Configuration-driven camera provider and lifecycle owner."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from capture_node.core.cameras.contracts import Camera, CameraDevice
from capture_node.core.cameras.manager import CameraManager
from capture_node.core.cameras.options import CameraOptions
from capture_node.core.cameras.registry import (
    CameraRegistry,
    default_camera_registry,
)
from capture_node.core.cameras.unavailable import UnavailableCamera
class ConfiguredCameraProvider:
    """Selects, exposes, replaces, and closes the configured camera."""

    def __init__(
        self,
        options: CameraOptions,
        *,
        tolerate_unavailable: bool = False,
        registry: CameraRegistry | None = None,
    ) -> None:
        self._registry = registry or default_camera_registry()
        self._options = options
        try:
            camera = self._registry.create(options)
        except BaseException as exc:
            if not tolerate_unavailable:
                raise
            logging.getLogger(__name__).warning("camera is unavailable: %s", exc)
            camera = UnavailableCamera(exc)
        self._manager = CameraManager(camera)

    def camera(self) -> Camera:
        return self._manager

    def devices(self) -> Sequence[CameraDevice]:
        return self._registry.devices()

    def replace(
        self,
        options: CameraOptions,
        parameters: Mapping[str, Any],
    ) -> None:
        previous = self._options
        self._manager.replace(
            lambda: self._configured_camera(options, parameters),
            lambda: self._fallback_camera(previous, parameters),
        )
        self._options = options

    def close(self) -> None:
        self._manager.close()

    def _configured_camera(
        self,
        options: CameraOptions,
        parameters: Mapping[str, Any],
    ) -> Camera:
        camera = self._registry.create(options)
        try:
            camera.apply(parameters)
        except BaseException:
            self._close_camera(camera)
            raise
        return camera

    def _fallback_camera(
        self,
        options: CameraOptions,
        parameters: Mapping[str, Any],
    ) -> Camera:
        try:
            return self._configured_camera(options, parameters)
        except BaseException as exc:
            logging.getLogger(__name__).warning(
                "camera rollback is unavailable: %s",
                exc,
            )
            return UnavailableCamera(exc)

    @staticmethod
    def _close_camera(camera: Camera) -> None:
        close = getattr(camera, "close", None)
        if close is not None:
            close()
