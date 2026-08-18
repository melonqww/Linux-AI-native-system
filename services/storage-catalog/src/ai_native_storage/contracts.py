"""Stable contracts shared with the runtime and desktop panel."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PermissionLevel(StrEnum):
    NONE = "none"
    METADATA = "metadata"
    CONTENT = "content"


class EntryType(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"


class CollectionKind(StrEnum):
    SMART = "smart"
    SNAPSHOT = "snapshot"


@dataclass(frozen=True)
class DiscoveredVolume:
    volume_id: str
    name: str
    mount_point: str
    device: str
    fs_type: str
    is_system: bool
    is_removable: bool
    is_network: bool
    total_bytes: int = 0
    free_bytes: int = 0


@dataclass(frozen=True)
class VolumeInfo:
    volume_id: str
    name: str
    mount_point: str
    device: str
    fs_type: str
    is_system: bool
    is_removable: bool
    is_network: bool
    is_available: bool
    permission: PermissionLevel
    total_bytes: int
    free_bytes: int
    last_seen_at: str


@dataclass(frozen=True)
class CatalogEntry:
    volume_id: str
    path: str
    stable_key: str
    name: str
    extension: str
    mime_type: str | None
    entry_type: EntryType
    role: str
    size_bytes: int
    mtime_ns: int
    sensitive: bool


@dataclass(frozen=True)
class CatalogScanReport:
    volume_id: str
    mount_point: str
    discovered: int
    removed: int
    inaccessible: int
    skipped_mounts: int
    duration_ms: float


@dataclass(frozen=True)
class FileQuery:
    name_contains: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    volume_ids: tuple[str, ...] = ()
    limit: int = 100


@dataclass(frozen=True)
class VirtualCollection:
    collection_id: str
    title: str
    kind: CollectionKind
    query: FileQuery | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CollectionItem:
    volume_id: str
    path: str
    stable_key: str
    name: str
    score: float | None = None
    snippet: str | None = None
    available: bool = True
