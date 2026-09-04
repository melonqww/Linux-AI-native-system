"""Public contracts for module discovery and capability routing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


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
class IntentRouteDescriptor:
    capability_id: str
    operation: str
    description: str
    examples: tuple[str, ...]


@dataclass(frozen=True)
class CapabilityContract:
    """Module-owned, non-authoritative description of one provided capability."""

    capability_id: str
    description: str
    input_schema: dict[str, Any]
    requested_permissions: tuple[str, ...]
    user_intent: IntentRouteDescriptor | None = None


@dataclass(frozen=True)
class ModuleManifest:
    schema_version: int
    module_id: str
    display_name: str
    description: str
    module_version: str
    core_api: str
    capabilities: tuple[CapabilityContract, ...]
    dependencies: tuple[str, ...]
    optional_dependencies: tuple[str, ...]
    requested_permissions: tuple[str, ...]
    lifecycle: str
    resource_class: str
    default_enabled: bool
    entrypoint: ModuleEntrypoint

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(item.capability_id for item in self.capabilities)

    @property
    def intent_routes(self) -> tuple[IntentRouteDescriptor, ...]:
        return tuple(
            item.user_intent
            for item in self.capabilities
            if item.user_intent is not None
        )


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
