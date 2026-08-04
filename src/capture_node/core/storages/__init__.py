"""Storage contracts, providers, and distribution."""

from capture_node.core.storages.contracts import (
    SaveResult,
    Storage,
    StorageListResult,
    StorageProvider,
    StoredFile,
)
from capture_node.core.storages.distribution import (
    DistributionInProgress,
    DistributionResult,
    Distributor,
    TargetFailure,
    TerminalState,
)
from capture_node.core.storages.options import (
    FileStorageOptions,
    FtpsStorageOptions,
    SftpStorageOptions,
    StorageOptions,
)
from capture_node.core.storages.provider import ConfiguredStorageProvider
from capture_node.core.storages.registry import (
    StorageRegistration,
    StorageRegistry,
    default_storage_registry,
)

__all__ = [
    "ConfiguredStorageProvider",
    "DistributionInProgress",
    "DistributionResult",
    "Distributor",
    "FileStorageOptions",
    "FtpsStorageOptions",
    "SaveResult",
    "SftpStorageOptions",
    "Storage",
    "StorageListResult",
    "StorageOptions",
    "StorageProvider",
    "StorageRegistration",
    "StorageRegistry",
    "TargetFailure",
    "TerminalState",
    "StoredFile",
    "default_storage_registry",
]
