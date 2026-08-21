from __future__ import annotations

from pathlib import Path

import pytest

from capture_node.core.storages import (
    ConfiguredStorageProvider,
    FileStorageOptions,
)
from capture_node.web.storage_browser import StorageBrowser


def test_storage_browser_pages_newest_files_and_resolves_download(
    tmp_path: Path,
) -> None:
    root = tmp_path / "captures"
    root.mkdir()
    for index in range(5):
        path = root / f"{index}.jpg"
        path.write_bytes(bytes([index]))
        path.touch()
    provider = ConfiguredStorageProvider(
        (
            FileStorageOptions(
                type="file",
                name="local",
                directory=root,
            ),
        )
    )
    browser = StorageBrowser(provider)

    result = browser.list_files("local", 2, 2)

    assert result.total == 5
    assert len(result.items) == 2
    assert browser.read_file("local", result.items[0].name)


def test_storage_browser_rejects_unknown_target_and_path_escape(
    tmp_path: Path,
) -> None:
    root = tmp_path / "captures"
    root.mkdir()
    provider = ConfiguredStorageProvider(
        (
            FileStorageOptions(
                type="file",
                name="local",
                directory=root,
            ),
        )
    )
    browser = StorageBrowser(provider)

    with pytest.raises(KeyError, match="unknown"):
        browser.list_files("missing", 1, 10)
    with pytest.raises(ValueError, match="filename"):
        browser.read_file("local", "../secret")


def test_file_storage_skips_symlinks_outside_its_root(tmp_path: Path) -> None:
    root = tmp_path / "captures"
    root.mkdir()
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"private")
    (root / "linked.jpg").symlink_to(outside)
    provider = ConfiguredStorageProvider(
        (
            FileStorageOptions(
                type="file",
                name="local",
                directory=root,
            ),
        )
    )
    browser = StorageBrowser(provider)

    result = browser.list_files("local", 1, 10)

    assert result.items == ()
    with pytest.raises(ValueError, match="outside"):
        browser.read_file("local", "linked.jpg")
