"""Storage-neutral logical-name and physical-layout rules."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath

from capture_node.core.storages.options import StorageLayout


def relative_storage_path(name: str, layout: StorageLayout) -> PurePosixPath:
    """Return a confined physical path for one logical storage name."""

    validate_storage_name(name)
    if layout == "daily":
        directory = capture_date_directory(name)
        if directory is not None:
            return PurePosixPath(directory, name)
    return PurePosixPath(name)


def storage_read_paths(name: str) -> tuple[PurePosixPath, ...]:
    """Return current and legacy physical paths for one logical name."""

    validate_storage_name(name)
    directory = capture_date_directory(name)
    if directory is None:
        return (PurePosixPath(name),)
    return (
        PurePosixPath(directory, name),
        PurePosixPath(name),
    )


def capture_date_directory(name: str) -> str | None:
    candidate = name[:8]
    if len(candidate) != 8 or not candidate.isdigit():
        return None
    try:
        datetime.strptime(candidate, "%Y%m%d")
    except ValueError:
        return None
    return candidate


def is_capture_date_directory(name: str) -> bool:
    return len(name) == 8 and capture_date_directory(name) == name


def validate_storage_name(name: str) -> None:
    if (
        not name
        or "\x00" in name
        or name in {".", ".."}
        or PurePosixPath(name).name != name
    ):
        raise ValueError("invalid storage filename")
