"""Capability Registry for the AI-native Linux user-space core."""

from .contracts import (
    CapabilityProvider,
    IntentRouteDescriptor,
    ModuleEntrypoint,
    ModuleManifest,
    ModuleState,
    RegisteredModule,
    SyncIssue,
    SyncReport,
)
from .manifest import ManifestValidationError, load_manifest
from .registry import CapabilityRegistry

__all__ = [
    "CapabilityProvider",
    "IntentRouteDescriptor",
    "CapabilityRegistry",
    "ManifestValidationError",
    "ModuleEntrypoint",
    "ModuleManifest",
    "ModuleState",
    "RegisteredModule",
    "SyncIssue",
    "SyncReport",
    "load_manifest",
]
