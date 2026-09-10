"""Immutable, bounded public contracts for the Security Center foundation."""

from __future__ import annotations

from dataclasses import dataclass, field


STATUS_SCHEMA_VERSION = 1
MODULE_ID = "security.center"
MODULE_VERSION = "0.1.0"
MODULE_LIFECYCLE = "on-demand"
STATUS_CAPABILITIES = ("security.module.status",)


@dataclass(frozen=True, slots=True)
class SecurityModuleStatus:
    """Public status returned by the foundation capability."""

    schema_version: int = field(init=False, default=STATUS_SCHEMA_VERSION)
    module_id: str = field(init=False, default=MODULE_ID)
    module_version: str = field(init=False, default=MODULE_VERSION)
    state: str = field(init=False, default="ready")
    lifecycle: str = field(init=False, default=MODULE_LIFECYCLE)
    capabilities: tuple[str, ...] = field(init=False, default=STATUS_CAPABILITIES)

    def to_dict(self) -> dict[str, object]:
        """Return a detached response containing JSON-native value types only."""

        return {
            "schema_version": self.schema_version,
            "module_id": self.module_id,
            "module_version": self.module_version,
            "state": self.state,
            "lifecycle": self.lifecycle,
            "capabilities": list(self.capabilities),
        }
