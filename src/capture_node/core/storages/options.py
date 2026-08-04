"""Validated configuration models for storage implementations."""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from capture_node.core.models import StrictModel

_ENVIRONMENT_VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
StorageLayout = Literal["flat", "daily"]


class FileStorageOptions(StrictModel):
    type: Literal["file"]
    name: str = Field(min_length=1)
    directory: Path
    layout: StorageLayout = "daily"


class SftpStorageOptions(StrictModel):
    type: Literal["sftp"]
    name: str = Field(min_length=1)
    host: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1)
    remote_directory: str
    private_key: Path | None = None
    password_env: str | None = None
    private_key_passphrase_env: str | None = None
    known_hosts: Path = Path("~/.ssh/known_hosts")
    connect_timeout_seconds: float = Field(default=10, gt=0)
    operation_timeout_seconds: float = Field(default=30, gt=0)
    layout: StorageLayout = "daily"

    @field_validator("remote_directory")
    @classmethod
    def validate_remote_directory(cls, value: str) -> str:
        if (
            not value.startswith("/")
            or "\x00" in value
            or posixpath.normpath(value) != value
        ):
            raise ValueError(
                "remote_directory must be a normalized absolute POSIX path"
            )
        return value

    @field_validator("password_env", "private_key_passphrase_env")
    @classmethod
    def validate_environment_variable(cls, value: str | None) -> str | None:
        if value is not None and not _ENVIRONMENT_VARIABLE.fullmatch(value):
            raise ValueError("secret environment variable name is invalid")
        return value

    @model_validator(mode="after")
    def validate_authentication(self) -> SftpStorageOptions:
        if self.private_key is None and self.password_env is None:
            raise ValueError("SFTP requires private_key or password_env")
        if self.private_key is None and self.private_key_passphrase_env is not None:
            raise ValueError(
                "private_key_passphrase_env requires a configured private_key"
            )
        return self


class FtpsStorageOptions(StrictModel):
    type: Literal["ftps"]
    name: str = Field(min_length=1)
    host: str = Field(min_length=1)
    port: int = Field(default=21, ge=1, le=65535)
    username: str = Field(min_length=1)
    remote_directory: str
    password_env: str
    verify_tls: bool = True
    ca_file: Path | None = None
    connect_timeout_seconds: float = Field(default=10, gt=0)
    operation_timeout_seconds: float = Field(default=30, gt=0)
    layout: StorageLayout = "daily"

    @field_validator("remote_directory")
    @classmethod
    def validate_remote_directory(cls, value: str) -> str:
        if (
            not value.startswith("/")
            or "\x00" in value
            or posixpath.normpath(value) != value
        ):
            raise ValueError(
                "remote_directory must be a normalized absolute POSIX path"
            )
        return value

    @field_validator("password_env")
    @classmethod
    def validate_password_environment_variable(cls, value: str) -> str:
        if not _ENVIRONMENT_VARIABLE.fullmatch(value):
            raise ValueError("secret environment variable name is invalid")
        return value

StorageOptions = FileStorageOptions | SftpStorageOptions | FtpsStorageOptions
