import json
import struct
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, TiffImagePlugin

from capture_node.core import CaptureNode
from capture_node.core.cameras import Capture, Picamera2Options, RawCapture
from capture_node.core.capture_metadata import with_capture_metadata
from capture_node.core.encodings.dng import encode_dng
from capture_node.core.encodings.options import DngImageOptions
from conftest import (
    FakeCamera,
    FakeCameraProvider,
    FakeStorage,
    FakeStorageProvider,
    fake_dng,
    make_configuration,
    wait_until,
)


def calibration():
    return {
        "image_width": 24,
        "image_height": 16,
        "camera_matrix": [[20, 0, 12], [0, 20, 8], [0, 0, 1]],
        "distortion_coefficients": {"k1": 0.1, "k2": -0.2, "p1": 0, "p2": 0, "k3": 0},
        "R": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "T_mm": [-30, 0, -6],
    }


def read_parameters(data, extension):
    with Image.open(BytesIO(data)) as image:
        image.load()
        if extension == "dng":
            return json.loads(image.tag_v2[270])
        return json.loads(image.getexif()[37510][8:])


@pytest.mark.parametrize("method", ["standalone", "bundle"])
def test_all_formats_embed_only_calibration_inputs_and_capture_settings(
    tmp_path, method
):
    document = calibration()
    document.update(
        camera_id="cam02",
        reference_camera="cam01",
        group_id="node1",
        model="opencv_pinhole",
        schema_version=1,
        transform_convention="X_camera = R @ X_reference + T_mm",
        distortion_coefficient_order=["k1", "k2", "p1", "p2", "k3"],
        sample_count=30,
        per_view_rms_px=[0.1],
        validation_note="omitted",
        source_observations_directory="private/path",
        baseline_mm=30.59,
    )
    config = make_configuration(
        tmp_path / "config.json",
        {
            "camera": {
                "type": "picamera2",
                "calibration": document,
                "params": {"AfMode": 0, "LensPosition": 5.6, "horizontal_flip": True},
            },
            "images": {"png": {}, "jpeg": {}, "dng": {}},
        },
    )
    storage = FakeStorage("local")
    node = CaptureNode(
        FakeCameraProvider(FakeCamera()), config, FakeStorageProvider(storage)
    )
    try:
        if method == "bundle":
            bundle = node.capture_bundle("pair-1", {"LensPosition": 6.0})
            try:
                artifacts = [
                    (artifact.data, artifact.name) for artifact in bundle.artifacts
                ]
                capture_id = bundle.capture_id
            finally:
                bundle.close()
        else:
            result = node.collect_once()
            assert result.error is None
            wait_until(lambda: not node.is_pending)
            artifacts = storage.calls
            capture_id = result.capture_id
        assert len(artifacts) == 3
        for data, name in artifacts:
            parameters = read_parameters(data, name.rsplit(".", 1)[1])
            assert parameters["calibration"] == calibration()
            assert parameters["captureId"] == capture_id
            assert parameters["ExposureTime"] == 12000
            settings = parameters["captureSettings"]
            assert (settings["imageWidth"], settings["imageHeight"]) == (24, 16)
            assert settings["horizontalFlip"] is True
            assert settings["verticalFlip"] is False
            assert settings["configuredControls"]["LensPosition"] == (
                6.0 if method == "bundle" else 5.6
            )
            if method == "bundle":
                assert parameters["requestId"] == "pair-1"
        assert node.options.camera.params["LensPosition"] == 5.6
    finally:
        node.stop()


def test_calibration_and_settings_snapshot_does_not_alias_configuration():
    camera = Picamera2Options(calibration=calibration(), params={"LensPosition": 5.6})
    capture = replace(FakeCamera().capture(), parameters={"LensPosition": 5.5999})
    result = with_capture_metadata(capture, camera, capture_id="test")
    camera.calibration["camera_matrix"][0][0] = 99
    camera.params["LensPosition"] = 7
    assert result.parameters["calibration"]["camera_matrix"][0][0] == 20
    assert (
        result.parameters["captureSettings"]["configuredControls"]["LensPosition"]
        == 5.6
    )
    assert result.parameters["LensPosition"] == 5.5999


@pytest.mark.parametrize("big_endian", [False, True])
def test_dng_metadata_preserves_pixels_and_original_tag_values(big_endian):
    image = Image.new("I;16B" if big_endian else "I;16", (24, 16), 1234)
    output = BytesIO()
    image.save(output, format="TIFF", tiffinfo={50706: bytes((1, 4, 0, 0)), 270: "old"})
    original = output.getvalue()
    capture = Capture(
        image, datetime.now(UTC), {"calibration": calibration()}, RawCapture(original)
    )
    encoded = encode_dng(capture, DngImageOptions())
    assert encoded[:4] == (b"MM\x00*" if big_endian else b"II*\x00")
    assert encoded[8 : len(original)] == original[8:]
    with Image.open(BytesIO(original)) as before, Image.open(BytesIO(encoded)) as after:
        assert after.tobytes() == before.tobytes()
        assert {k: v for k, v in after.tag_v2.items() if k != 270} == {
            k: v for k, v in before.tag_v2.items() if k != 270
        }
        assert json.loads(after.tag_v2[270])["calibration"] == calibration()


def test_dng_embedding_disabled_preserves_native_artifact():
    capture = replace(FakeCamera().capture(), raw=RawCapture(fake_dng()))
    assert encode_dng(capture, DngImageOptions(embedMetadata=False)) == capture.raw.data


@pytest.mark.parametrize(
    "data",
    [
        b"invalid",
        b"II+\x00" + b"\x00" * 12,
        b"II*\x00\xff\xff\xff\x7f",
        b"II*\x00\x08\x00\x00\x00\x01\x00",
    ],
)
def test_invalid_dng_cannot_silently_lose_metadata(data):
    capture = replace(FakeCamera().capture(), raw=RawCapture(data))
    with pytest.raises(ValueError):
        encode_dng(capture, DngImageOptions())


def test_installed_pidng_bayer_fixture_retains_sensor_strips():
    # Generated by the device's PiDNG RAW2DNG writer: 32x24 uint16 arange,
    # RGGB CFA, uncompressed, through the same BytesIO path as Picamera2.
    original = (
        Path(__file__).parent / "fixtures" / "pidng-bayer-32x24.dng"
    ).read_bytes()
    capture = replace(
        FakeCamera().capture(),
        raw=RawCapture(original),
        parameters={"calibration": calibration()},
    )
    encoded = encode_dng(capture, DngImageOptions())

    def read_tags(data):
        stream = BytesIO(data)
        directory = TiffImagePlugin.ImageFileDirectory_v2(stream.read(8))
        stream.seek(directory.next)
        directory.load(stream)
        return dict(directory)

    before, after = read_tags(original), read_tags(encoded)
    assert {key: value for key, value in after.items() if key != 270} == before
    assert json.loads(after[270])["calibration"] == calibration()
    strips = b"".join(
        encoded[offset : offset + length]
        for offset, length in zip(after[273], after[279], strict=True)
    )
    assert strips == struct.pack("<768H", *range(768))


def test_legacy_distortion_array_uses_declared_names_without_storing_order():
    from capture_node.core.cameras.calibration import compact_calibration

    source = calibration()
    source["distortion_coefficient_order"] = ["k3", "k2", "k1", "p2", "p1"]
    source["distortion_coefficients"] = [0, -0.2, 0.1, 0, 0]
    assert compact_calibration(source) == calibration()


@pytest.mark.parametrize(
    "coefficients",
    [
        {"k1": 0.1},
        {"k1": 0, "k2": 0, "p1": 0, "p2": 0, "k3": float("nan")},
        [1, 2, 3, 4],
        {"k1": "0.1", "k2": 0, "p1": 0, "p2": 0, "k3": 0},
    ],
)
def test_invalid_named_calibration_is_rejected_at_configuration_boundary(coefficients):
    with pytest.raises(ValueError):
        Picamera2Options(calibration={"distortion_coefficients": coefficients})
