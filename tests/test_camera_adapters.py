from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from capture_node.core.cameras.picamera2 import (
    Picamera2Camera,
    discover_picamera2_devices,
)


class FakeRequest:
    def __init__(self, calls: list[tuple[str, str]]) -> None:
        self._calls = calls

    def make_array(self, stream: str):
        self._calls.append(("still", stream))
        return object()

    def get_metadata(self):
        return {"ExposureTime": 5_000}

    def save_dng(self, output, stream: str) -> None:
        self._calls.append(("raw", stream))
        output.write(b"test-dng")

    def release(self) -> None:
        pass


class FakeTransform:
    def __init__(self, *, hflip: bool, vflip: bool) -> None:
        self.hflip = hflip
        self.vflip = vflip


class FakePicamera:
    instances: list["FakePicamera"] = []

    def __init__(self, camera_num: int = 0) -> None:
        self.camera_num = camera_num
        self.calls: list[tuple[str, str]] = []
        self.configured = None
        self.applied_controls = {}
        self.sensor_resolution = (4608, 2592)
        self.sensor_modes = [
            {"size": (4608, 2592), "bit_depth": 10},
            {"size": (4608, 2592), "bit_depth": 8},
        ]
        self.__class__.instances.append(self)

    def create_preview_configuration(self, *, main, transform):
        return {"main": main, "transform": transform}

    def configure(self, configuration) -> None:
        self.configured = configuration
        self.camera_config = configuration

    def create_still_configuration(self, *, main, raw, sensor, transform):
        return {
            "main": main,
            "raw": raw,
            "sensor": sensor,
            "transform": transform,
        }

    def switch_mode_capture_request_and_stop(self, configuration):
        self.still_config = configuration
        return FakeRequest(self.calls)

    def start(self) -> None:
        pass

    def capture_request(self) -> FakeRequest:
        return FakeRequest(self.calls)

    def capture_array(self, stream: str):
        self.calls.append(("preview", stream))
        return object()

    def set_controls(self, parameters) -> None:
        self.applied_controls = dict(parameters)

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass

    @staticmethod
    def global_camera_info():
        return [
            {
                "Num": 0,
                "Model": "imx708_noir",
                "Id": "/base/axi/i2c@88000/imx708@1a",
            },
            {
                "Num": 1,
                "Model": "imx219",
                "Id": "/base/axi/i2c@80000/imx219@10",
            },
            {
                "Num": 2,
                "Model": "USB Camera",
                "Id": "/base/axi/usb@300000/camera",
            },
        ]


@pytest.mark.covers("CAP-003", "IF-CAP-001", "IF-CAP-004", "WEB-003")
def test_x4_picamera_still_and_preview_share_the_main_source(
    monkeypatch,
) -> None:
    FakePicamera.instances.clear()
    monkeypatch.setitem(
        sys.modules,
        "picamera2",
        SimpleNamespace(Picamera2=FakePicamera),
    )
    monkeypatch.setitem(
        sys.modules,
        "libcamera",
        SimpleNamespace(Transform=FakeTransform),
    )
    monkeypatch.setattr(
        "capture_node.core.cameras.picamera2.Image.fromarray",
        lambda _array: Image.new("RGB", (8, 6), "green"),
    )
    camera = Picamera2Camera(camera_num=1, preview_fps=1000)

    still = camera.capture()
    preview = next(camera.frames())
    camera.apply(
        {
            "horizontal_flip": True,
            "vertical_flip": True,
            "ExposureTime": 5_000,
        }
    )
    controls = camera.controls()
    camera.close()

    implementation = FakePicamera.instances[0]
    assert implementation.camera_num == 1
    assert implementation.configured["main"]["format"] == "BGR888"
    assert implementation.configured["transform"].hflip
    assert implementation.configured["transform"].vflip
    assert implementation.applied_controls == {"ExposureTime": 5_000}
    assert implementation.calls == [("still", "main"), ("preview", "main")]
    assert still.image.size == (8, 6)
    assert preview.media_type == "image/jpeg"
    assert [(control.name, control.value) for control in controls] == [
        ("horizontal_flip", True),
        ("vertical_flip", True),
    ]


def test_picamera_discovery_returns_selectable_options(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "picamera2",
        SimpleNamespace(Picamera2=FakePicamera),
    )

    devices = discover_picamera2_devices()

    assert [device.identifier for device in devices] == [
        "picamera2:0",
        "picamera2:1",
    ]
    assert [device.name for device in devices] == ["imx708_noir", "imx219"]
    assert [device.options.camera_num for device in devices] == [0, 1]
    assert devices[0].options.size == (1920, 1080)


def test_picamera_raw_capture_returns_dng_from_full_sensor_mode(monkeypatch) -> None:
    FakePicamera.instances.clear()
    monkeypatch.setitem(
        sys.modules,
        "picamera2",
        SimpleNamespace(Picamera2=FakePicamera),
    )
    monkeypatch.setitem(
        sys.modules,
        "libcamera",
        SimpleNamespace(Transform=FakeTransform),
    )
    monkeypatch.setattr(
        "capture_node.core.cameras.picamera2.Image.fromarray",
        lambda _array: Image.new("RGB", (8, 6), "green"),
    )
    camera = Picamera2Camera(camera_num=0, size=(1920, 1080))

    capture = camera.capture(include_raw=True)

    implementation = FakePicamera.instances[0]
    assert capture.raw is not None
    assert capture.raw.data == b"test-dng"
    assert capture.raw.extension == "dng"
    assert implementation.calls == [("still", "main"), ("raw", "raw")]
    assert implementation.still_config["main"] == {
        "format": "BGR888",
        "size": (1920, 1080),
    }
    assert implementation.still_config["sensor"] == {
        "output_size": (4608, 2592),
        "bit_depth": 10,
    }


@pytest.mark.covers("NFR-001")
def test_x5_importing_core_does_not_load_platform_camera_modules() -> None:
    command = (
        "import sys; import capture_node.core; "
        "raise SystemExit(1 if 'picamera2' in sys.modules else 0)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", command],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
