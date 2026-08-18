"""Public contracts for module discovery and capability routing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ModuleState(StrEnum):
    INSTALLED = "installed"
    ENABLED = "enabled"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    QUARANTINED = "quarantined"


@dataclass(frozen=True)
class ModuleEntrypoint:
    kind: str
    module: str
    python_path: str


@dataclass(frozen=True)
class ModuleManifest:
    schema_version: int
    module_id: str
    display_name: str
    description: str
    module_version: str
    core_api: str
    capabilities: tuple[str, ...]
    dependencies: tuple[str, ...]
    optional_dependencies: tuple[str, ...]
    requested_permissions: tuple[str, ...]
    lifecycle: str
    resource_class: str
    default_enabled: bool
    entrypoint: ModuleEntrypoint


@dataclass(frozen=True)
class RegisteredModule:
    manifest: ModuleManifest
    manifest_path: str
    desired_enabled: bool
    state: ModuleState
    state_reason: str | None
    quarantined: bool
    last_seen_at: str


@dataclass(frozen=True)
class CapabilityProvider:
    capability_id: str
    module_id: str
    module_version: str
    state: ModuleState


@dataclass(frozen=True)
class SyncIssue:
    path: str
    error: str


@dataclass(frozen=True)
class SyncReport:
    scanned: int
    registered: int
    updated: int
    issues: tuple[SyncIssue, ...]
