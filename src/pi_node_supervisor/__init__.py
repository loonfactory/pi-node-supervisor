"""Raspberry Pi local node supervisor, schema v2."""

from .config_v2 import Resolution, resolve_configuration
from .service_v2 import SupervisorServiceV2

__all__ = ["Resolution", "SupervisorServiceV2", "resolve_configuration"]
__version__ = "0.2.0"
