from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from capture_node.core.cameras.v4l2 import V4L2Camera, extract_jpeg_frames
from capture_node.core.cameras.v4l2 import (
    discover_v4l2_devices,
    parse_v4l2_controls,
)


def jpeg(color: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (12, 8), color).save(output, "JPEG")
    return output.getvalue()


class FakeProcess:
    def __init__(self, payload: bytes) -> None:
        self.stdout = BytesIO(payload)
        self.stderr = BytesIO()
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode


def test_mjpeg_parser_preserves_partial_trailing_frame() -> None:
    first = jpeg("red")
    second = jpeg("blue")
    split = len(second) // 2

    frames, remainder = extract_jpeg_frames(b"noise" + first + second[:split])
    trailing, remainder = extract_jpeg_frames(remainder + second[split:])

    assert frames == [first]
    assert trailing == [second]
    assert remainder == b""


@pytest.mark.covers("CAP-006", "IF-CAP-001", "IF-CAP-004", "IF-CAP-005", "WEB-003")
def test_v4l2_camera_shares_mjpeg_source_and_applies_controls(
    tmp_path: Path,
) -> None:
    device = tmp_path / "video0"
    device.touch()
    source = jpeg("green")
    process = FakeProcess(source)
    process_calls: list[tuple[list[str], dict]] = []
    control_calls: list[tuple[list[str], dict]] = []

    def process_factory(command, **kwargs):
        process_calls.append((command, kwargs))
        return process

    def control_runner(command, **kwargs):
        control_calls.append((command, kwargs))
        if "--list-ctrls-menus" in command:
            return SimpleNamespace(
                returncode=0,
                stdout="""
 gain 0x00980913 (int) : min=0 max=255 step=1 default=64 value=28
 auto_exposure 0x009a0901 (menu) : min=0 max=3 default=3 value=3
        1: Manual Mode
        3: Aperture Priority Mode
 exposure_time_absolute 0x009a0902 (int) : min=3 max=2047 step=1 default=166 value=83 flags=inactive
""",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    camera = V4L2Camera(
        device=device,
        width=1280,
        height=720,
        frame_rate=15,
        process_factory=process_factory,
        control_runner=control_runner,
    )
    camera.apply({"brightness": 140, "white_balance_automatic": False})

    captured = camera.capture()
    preview = next(camera.frames())
    camera.close()

    command = process_calls[0][0]
    assert command[command.index("-input_format") + 1] == "mjpeg"
    assert command[command.index("-video_size") + 1] == "1280x720"
    assert command[command.index("-i") + 1] == str(device)
    assert preview.data == source
    assert captured.image.size == (12, 8)
    assert captured.parameters["brightness"] == 140
    assert captured.parameters["observed_controls"] == {
        "gain": 28,
        "auto_exposure": 3,
        "exposure_time_absolute": 83,
    }
    assert captured.parameters["control_snapshot_at"].endswith("+00:00")
    assert "--set-ctrl=brightness=140,white_balance_automatic=0" in control_calls[0][0]


def test_v4l2_camera_rejects_unsafe_control_names(tmp_path: Path) -> None:
    device = tmp_path / "video0"
    device.touch()
    camera = V4L2Camera(
        device=device,
        process_factory=lambda _command, **_kwargs: FakeProcess(jpeg("white")),
    )

    with pytest.raises(ValueError, match="control name"):
        camera.apply({"brightness;reboot": 1})
    camera.close()


def test_v4l2_camera_rejects_stale_capture_after_device_disconnect(
    tmp_path: Path,
) -> None:
    device = tmp_path / "video0"
    device.touch()
    camera = V4L2Camera(
        device=device,
        process_factory=lambda _command, **_kwargs: FakeProcess(jpeg("white")),
    )
    device.unlink()

    assert not camera.status().connected
    with pytest.raises(RuntimeError, match="disconnected"):
        camera.capture()
    camera.close()


def test_v4l2_control_snapshot_failure_does_not_discard_capture(
    tmp_path: Path,
) -> None:
    device = tmp_path / "video0"
    device.touch()

    def failing_control_runner(_command, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="control query failed")

    camera = V4L2Camera(
        device=device,
        process_factory=lambda _command, **_kwargs: FakeProcess(jpeg("white")),
        control_runner=failing_control_runner,
    )

    captured = camera.capture()

    assert captured.image.size == (12, 8)
    assert captured.parameters["control_snapshot_error"] == (
        "cannot read V4L2 controls: control query failed"
    )
    assert "observed_controls" not in captured.parameters
    camera.close()


def test_v4l2_control_parser_exposes_live_ranges_and_menus() -> None:
    controls = parse_v4l2_controls(
        """
 brightness 0x00980900 (int) : min=0 max=255 step=1 default=128 value=140 flags=has-min-max
 white_balance_automatic 0x0098090c (bool) : default=1 value=1
 power_line_frequency 0x00980918 (menu) : min=0 max=2 default=2 value=2
        0: Disabled
        1: 50 Hz
        2: 60 Hz
 white_balance_temperature 0x0098091a (int) : min=2800 max=6500 step=1 default=4000 value=4000 flags=inactive, has-min-max
"""
    )

    assert controls[0].name == "brightness"
    assert controls[0].value == 140
    assert controls[0].maximum == 255
    assert controls[1].value is True
    assert controls[2].options == {0: "Disabled", 1: "50 Hz", 2: "60 Hz"}
    assert controls[3].inactive is True


def test_v4l2_discovery_excludes_non_capture_nodes(tmp_path: Path) -> None:
    sys_class = tmp_path / "video4linux"
    for name, label in (("video0", "USB Camera"), ("video1", "USB Metadata")):
        entry = sys_class / name
        entry.mkdir(parents=True)
        (entry / "name").write_text(label, encoding="utf-8")

    def runner(command, **_kwargs):
        device = command[command.index("--device") + 1]
        return SimpleNamespace(
            returncode=0 if device.endswith("video0") else 1,
            stdout="",
            stderr="",
        )

    devices = discover_v4l2_devices(sys_class=sys_class, runner=runner)

    assert [(str(item.path), item.name) for item in devices] == [
        ("/dev/video0", "USB Camera")
    ]
