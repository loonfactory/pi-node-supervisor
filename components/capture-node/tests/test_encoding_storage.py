from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from capture_node.core.cameras import Capture
from capture_node.core.encodings import (
    EncodedImage,
    ImageEncoder,
    JpegEncoding,
    JpegImageOptions,
    PngImageOptions,
)
from capture_node.core.storages import Distributor, SaveResult, TerminalState
from capture_node.core.storages.file_storage import FileStorage
from conftest import FakeStorage, FakeStorageProvider


def sample_capture() -> Capture:
    return Capture(
        Image.new("RGB", (32, 20), "teal"),
        datetime(2026, 7, 24, 10, 11, 12, tzinfo=UTC),
        {
            "ExposureTime": 10_000,
            "AnalogueGain": 2.0,
            "Mode": "test",
            "observedControls": {
                "gain": 28,
                "exposure_time_absolute": 83,
            },
        },
    )


@pytest.mark.covers("CAP-008", "CAP-009")
def test_b1_encodes_jpeg_with_standard_metadata() -> None:
    encoded = ImageEncoder().encode(
        sample_capture(),
        JpegImageOptions(quality=83, embedMetadata=True),
    )
    image = Image.open(BytesIO(encoded.data))
    exif = image.getexif()

    assert encoded.extension == "jpeg"
    assert image.format == "JPEG"
    assert exif[36867] == "2026-07-24T10:11:12+00:00"
    assert json.loads(exif[37510][8:])["Mode"] == "test"


@pytest.mark.covers("CAP-008", "CAP-009")
def test_b1_2_encodes_png_with_readable_metadata() -> None:
    encoded = ImageEncoder().encode(
        sample_capture(),
        PngImageOptions(compression=9, embedMetadata=True),
    )
    image = Image.open(BytesIO(encoded.data))
    exif = image.getexif()

    assert encoded.extension == "png"
    assert image.format == "PNG"
    assert exif[36867] == "2026-07-24T10:11:12+00:00"
    parameters = json.loads(exif[37510][8:])
    assert parameters["Mode"] == "test"
    assert parameters["observedControls"] == {
        "gain": 28,
        "exposure_time_absolute": 83,
    }
    assert not image.text


@pytest.mark.covers("CAP-009")
def test_b1_3_metadata_can_be_omitted() -> None:
    for options in (
        JpegImageOptions(embedMetadata=False),
        PngImageOptions(embedMetadata=False),
    ):
        encoded = ImageEncoder().encode(
            sample_capture(),
            options,
        )
        image = Image.open(BytesIO(encoded.data))
        assert not image.getexif()
        assert not getattr(image, "text", {})


def test_image_encoder_rejects_duplicate_formats() -> None:
    with pytest.raises(ValueError, match="already exists"):
        ImageEncoder((JpegEncoding(), JpegEncoding()))


def test_image_encoder_rejects_an_unregistered_format() -> None:
    with pytest.raises(ValueError, match="unsupported image format"):
        ImageEncoder(()).encode(sample_capture(), PngImageOptions())


def test_image_encoder_uses_the_registered_implementation() -> None:
    class StubJpegEncoding:
        format = "jpeg"
        extension = "test"

        def encode(self, capture, options) -> EncodedImage:
            return EncodedImage(b"registered implementation", self.extension)

    encoder = ImageEncoder((StubJpegEncoding(),))

    encoded = encoder.encode(
        sample_capture(),
        JpegImageOptions(),
    )

    assert encoded == EncodedImage(b"registered implementation", "test")


@pytest.mark.covers("STO-008", "IF-STO-002", "IF-STO-005", "IF-STO-006")
def test_x2_file_storage_returns_after_write_and_close(tmp_path: Path) -> None:
    storage = FileStorage("local", tmp_path)

    result = storage.save(b"capture-data", "one.bin")

    assert result.succeeded
    assert (tmp_path / "one.bin").read_bytes() == b"capture-data"
    assert storage.read("one.bin") == b"capture-data"
    assert storage.list(1, 10).items[0].name == "one.bin"


def test_file_storage_uses_daily_directories_and_reads_legacy_files(
    tmp_path: Path,
) -> None:
    name = "20260731T131120.000000Z_capture.jpg"
    (tmp_path / "legacy.jpg").write_bytes(b"legacy")
    storage = FileStorage("local", tmp_path)

    result = storage.save(b"daily", name)
    listing = storage.list(1, 10)

    assert result.succeeded
    assert (tmp_path / "20260731" / name).read_bytes() == b"daily"
    assert storage.read(name) == b"daily"
    assert storage.read("legacy.jpg") == b"legacy"
    assert {item.name for item in listing.items} == {name, "legacy.jpg"}

    storage.delete(name)

    assert not (tmp_path / "20260731").exists()
    with pytest.raises(FileNotFoundError):
        storage.delete(name)


def test_file_storage_can_explicitly_use_a_flat_layout(tmp_path: Path) -> None:
    name = "20260731T131120.000000Z_capture.jpg"
    storage = FileStorage("local", tmp_path, layout="flat")

    result = storage.save(b"flat", name)

    assert result.succeeded
    assert (tmp_path / name).read_bytes() == b"flat"
    assert storage.read(name) == b"flat"


@pytest.mark.covers("STO-001", "STO-002", "STO-003", "IF-STO-004")
def test_b1_distributor_attempts_all_targets() -> None:
    first = FakeStorage("first")
    second = FakeStorage("second")

    result = Distributor(FakeStorageProvider(first, second)).distribute(
        b"x",
        "x.jpg",
    )

    assert result.state is TerminalState.COMPLETED
    assert result.targets == ("first", "second")
    assert len(first.calls) == len(second.calls) == 1


@pytest.mark.covers("STO-001", "STO-003")
def test_b2_no_target_distribution_completes() -> None:
    result = Distributor(FakeStorageProvider()).distribute(b"x", "x.jpg")
    assert result.state is TerminalState.COMPLETED
    assert result.targets == ()


@pytest.mark.covers(
    "STO-003",
    "STO-004",
    "STO-005",
    "STO-006",
    "STO-007",
    "IF-STO-001",
    "IF-STO-003",
)
def test_c4_failure_isolated_without_rollback_or_retry() -> None:
    succeeded = FakeStorage("succeeded")
    failed = FakeStorage(
        "failed",
        result=SaveResult.failure(OSError("target offline")),
    )
    trailing = FakeStorage("trailing")

    result = Distributor(
        FakeStorageProvider(succeeded, failed, trailing)
    ).distribute(b"x", "x.jpg")

    assert result.state is TerminalState.DISCARDED
    assert [failure.target for failure in result.failures] == ["failed"]
    assert len(succeeded.calls) == len(failed.calls) == len(trailing.calls) == 1


@pytest.mark.covers("STO-003", "STO-005", "IF-STO-003")
def test_c3_storage_exception_becomes_failure() -> None:
    storage = FakeStorage("raises", exception=TimeoutError("deadline"))
    result = Distributor(FakeStorageProvider(storage)).distribute(b"x", "x.jpg")

    assert result.state is TerminalState.DISCARDED
    assert isinstance(result.failures[0].cause, TimeoutError)


def test_save_result_invariants() -> None:
    with pytest.raises(ValueError):
        SaveResult(True, RuntimeError())
    with pytest.raises(ValueError):
        SaveResult(False)
