"""Durable software installation tasks backed by snapd."""

from .catalog import get_application, list_applications
from .contracts import (
    Application,
    ApplicationOption,
    BackendProgress,
    InstallPreferences,
    SoftwareTask,
)
from .manager import SoftwareManager
from .snapd import SnapdClient, SnapdError
from .store import SoftwareTaskStore

__all__ = [
    "Application",
    "ApplicationOption",
    "BackendProgress",
    "InstallPreferences",
    "SnapdClient",
    "SnapdError",
    "SoftwareManager",
    "SoftwareTask",
    "SoftwareTaskStore",
    "get_application",
    "list_applications",
]
