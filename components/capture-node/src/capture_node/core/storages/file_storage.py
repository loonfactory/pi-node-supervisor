"""Local filesystem storage adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from capture_node.core.storages.contracts import (
    SaveResult,
    StorageListResult,
    StoredFile,
)
from capture_node.core.storages.options import FileStorageOptions, StorageLayout
from capture_node.core.storages.paging import newest_page
from capture_node.core.storages.paths import (
    is_capture_date_directory,
    relative_storage_path,
    storage_read_paths,
)


class FileStorage:
    """Local filesystem storage with confined read and list access."""

    @classmethod
    def from_options(cls, options: FileStorageOptions) -> FileStorage:
        return cls(options.name, options.directory, options.layout)

    def __init__(
        self,
        name: str,
        directory: Path,
        layout: StorageLayout = "daily",
    ) -> None:
        self._name = name
        self._directory = directory
        self._layout = layout

    @property
    def name(self) -> str:
        return self._name

    def save(self, data: bytes, name: str) -> SaveResult:
        try:
            root = self._root(create=True)
            path = self._path(
                root,
                relative_storage_path(name, self._layout),
                require_exists=False,
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as stream:
                stream.write(data)
            return SaveResult.success()
        except BaseException as exc:
            return SaveResult.failure(exc)

    def read(self, name: str) -> bytes:
        root = self._root()
        for relative in storage_read_paths(name):
            try:
                path = self._path(root, relative, require_exists=True)
            except FileNotFoundError:
                continue
            if not path.is_file():
                raise FileNotFoundError(f"storage entry is not a file: {name}")
            return path.read_bytes()
        raise FileNotFoundError(f"storage file does not exist: {name}")

    def list(self, page: int, page_size: int) -> StorageListResult:
        root = self._root()
        return newest_page(self._files(root), page, page_size)

    def delete(self, name: str) -> None:
        root = self._root()
        for relative in storage_read_paths(name):
            try:
                path = self._path(root, relative, require_exists=True)
            except FileNotFoundError:
                continue
            if not path.is_file():
                raise FileNotFoundError(f"storage entry is not a file: {name}")
            path.unlink()
            self._remove_empty_daily_directory(root, path.parent)
            return
        raise FileNotFoundError(f"storage file does not exist: {name}")

    def _root(self, *, create: bool = False) -> Path:
        if create:
            self._directory.mkdir(parents=True, exist_ok=True)
        return self._directory.expanduser().resolve()

    def _files(self, root: Path):
        if not root.is_dir():
            return
        for entry in root.iterdir():
            if entry.is_symlink():
                continue
            if entry.is_file():
                stored = self._stored_file(root, entry)
                if stored is not None:
                    yield stored
                continue
            if not entry.is_dir() or not is_capture_date_directory(entry.name):
                continue
            for nested in entry.iterdir():
                if nested.is_symlink() or not nested.is_file():
                    continue
                stored = self._stored_file(root, nested)
                if stored is not None:
                    yield stored

    @staticmethod
    def _stored_file(root: Path, path: Path) -> StoredFile | None:
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
            status = resolved.stat()
            return StoredFile(
                name=resolved.name,
                size=status.st_size,
                modified_at=datetime.fromtimestamp(status.st_mtime, UTC),
            )
        except (OSError, ValueError):
            return None

    @staticmethod
    def _path(
        root: Path,
        relative: PurePosixPath,
        *,
        require_exists: bool,
    ) -> Path:
        path = (root / Path(*relative.parts)).resolve(strict=require_exists)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("storage file is outside the configured target") from exc
        return path

    @staticmethod
    def _remove_empty_daily_directory(root: Path, directory: Path) -> None:
        if directory == root or not is_capture_date_directory(directory.name):
            return
        try:
            directory.rmdir()
        except OSError:
            pass
