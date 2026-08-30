"""Durable software installation tasks backed by snapd."""

from .catalog import get_application, list_applications
from .backups import BackupManager, BackupStore
from .contracts import (
    Application,
    ApplicationOption,
    BackendProgress,
    BackupRecord,
    ApplicationRuntime,
    InstallPreferences,
    RemovalPreferences,
    RuntimeStatus,
    SnapshotSummary,
    SoftwareTask,
)
from .manager import SoftwareManager
from .connectivity import NetworkManagerMonitor
from .notifications import LinuxDesktopNotifier, NotificationDeliveryError
from .providers import SnapdProvider
from .recovery import RecoveryWorker
from .runtime import ApplicationLifecycleManager, DesktopRuntimeAdapter
from .snapd import SnapdClient, SnapdError
from .store import SoftwareTaskStore

__all__ = [
    "Application",
    "ApplicationOption",
    "BackendProgress",
    "BackupManager",
    "BackupRecord",
    "BackupStore",
    "ApplicationLifecycleManager",
    "ApplicationRuntime",
    "DesktopRuntimeAdapter",
    "InstallPreferences",
    "LinuxDesktopNotifier",
    "NetworkManagerMonitor",
    "NotificationDeliveryError",
    "RemovalPreferences",
    "RecoveryWorker",
    "RuntimeStatus",
    "SnapshotSummary",
    "SnapdClient",
    "SnapdProvider",
    "SnapdError",
    "SoftwareManager",
    "SoftwareTask",
    "SoftwareTaskStore",
    "get_application",
    "list_applications",
]
