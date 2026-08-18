"""Persistent registry of discovered storage and user-granted permissions."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .contracts import PermissionLevel, VolumeInfo
from .database import StorageDatabase
from .discovery import PlatformVolumeDiscovery, VolumeDiscovery


def _now() -> str:
    return datetime.now(UTC).isoformat()


class VolumeRegistry:
    def __init__(
        self,
        database_path: Path,
        *,
        discovery: VolumeDiscovery | None = None,
    ) -> None:
        self.database = StorageDatabase(database_path)
        self.discovery = discovery or PlatformVolumeDiscovery()

    def refresh(self) -> list[VolumeInfo]:
        discovered = self.discovery.discover()
        timestamp = _now()
        with self.database.connect() as connection:
            connection.execute("UPDATE volumes SET is_available = 0")
            for volume in discovered:
                default_permission = (
                    PermissionLevel.CONTENT if volume.is_system else PermissionLevel.NONE
                )
                connection.execute(
                    """
                    INSERT INTO volumes(
                        volume_id, name, mount_point, device, fs_type, is_system,
                        is_removable, is_network, is_available, permission,
                        total_bytes, free_bytes, detected_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    ON CONFLICT(volume_id) DO UPDATE SET
                        name = excluded.name,
                        mount_point = excluded.mount_point,
                        device = excluded.device,
                        fs_type = excluded.fs_type,
                        is_system = excluded.is_system,
                        is_removable = excluded.is_removable,
                        is_network = excluded.is_network,
                        is_available = 1,
                        total_bytes = excluded.total_bytes,
                        free_bytes = excluded.free_bytes,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (
                        volume.volume_id,
                        volume.name,
                        volume.mount_point,
                        volume.device,
                        volume.fs_type,
                        volume.is_system,
                        volume.is_removable,
                        volume.is_network,
                        default_permission.value,
                        volume.total_bytes,
                        volume.free_bytes,
                        timestamp,
                        timestamp,
                    ),
                )
        return self.list_volumes()

    def set_permission(self, volume_id: str, permission: PermissionLevel) -> VolumeInfo:
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE volumes SET permission = ? WHERE volume_id = ?",
                (permission.value, volume_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown volume: {volume_id}")
        return self.get_volume(volume_id)

    def get_volume(self, volume_id: str) -> VolumeInfo:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM volumes WHERE volume_id = ?", (volume_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown volume: {volume_id}")
        return self._from_row(row)

    def list_volumes(self, *, available_only: bool = False) -> list[VolumeInfo]:
        query = "SELECT * FROM volumes"
        if available_only:
            query += " WHERE is_available = 1"
        query += " ORDER BY is_system DESC, name, mount_point"
        with self.database.connect() as connection:
            rows = connection.execute(query).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: object) -> VolumeInfo:
        return VolumeInfo(
            volume_id=str(row["volume_id"]),
            name=str(row["name"]),
            mount_point=str(row["mount_point"]),
            device=str(row["device"]),
            fs_type=str(row["fs_type"]),
            is_system=bool(row["is_system"]),
            is_removable=bool(row["is_removable"]),
            is_network=bool(row["is_network"]),
            is_available=bool(row["is_available"]),
            permission=PermissionLevel(str(row["permission"])),
            total_bytes=int(row["total_bytes"]),
            free_bytes=int(row["free_bytes"]),
            last_seen_at=str(row["last_seen_at"]),
        )
