"""Explicit disk enrollment kept in the storage module, outside the core."""

from __future__ import annotations

from .contracts import PermissionLevel, StorageEnrollmentView, VolumeInfo
from .registry import VolumeRegistry


class StorageEnrollment:
    def __init__(self, registry: VolumeRegistry) -> None:
        self.registry = registry

    def discover(self) -> tuple[StorageEnrollmentView, ...]:
        return tuple(self._view(volume) for volume in self.registry.refresh())

    def list(self, *, available_only: bool = False) -> tuple[StorageEnrollmentView, ...]:
        return tuple(
            self._view(volume)
            for volume in self.registry.list_volumes(available_only=available_only)
        )

    def set_permission(
        self, volume_id: str, permission: PermissionLevel
    ) -> StorageEnrollmentView:
        return self._view(self.registry.set_permission(volume_id, permission))

    @staticmethod
    def _view(volume: VolumeInfo) -> StorageEnrollmentView:
        return StorageEnrollmentView(
            volume_id=volume.volume_id,
            name=volume.name,
            mount_point=volume.mount_point,
            fs_type=volume.fs_type,
            is_system=volume.is_system,
            is_removable=volume.is_removable,
            is_network=volume.is_network,
            is_available=volume.is_available,
            permission=volume.permission,
            permission_required=not volume.is_system
            and volume.permission is PermissionLevel.NONE,
            total_bytes=volume.total_bytes,
            free_bytes=volume.free_bytes,
        )
