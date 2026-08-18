"""Detect volume availability changes without granting new permissions."""

from __future__ import annotations

from ai_native_storage import VolumeRegistry

from .contracts import VolumeChange


class VolumeMonitor:
    def __init__(self, registry: VolumeRegistry) -> None:
        self.registry = registry
        self._available = {
            volume.volume_id for volume in registry.list_volumes(available_only=True)
        }

    def poll(self) -> VolumeChange:
        refreshed = self.registry.refresh()
        available = {volume.volume_id for volume in refreshed if volume.is_available}
        change = VolumeChange(
            connected=tuple(sorted(available - self._available)),
            disconnected=tuple(sorted(self._available - available)),
        )
        self._available = available
        return change
