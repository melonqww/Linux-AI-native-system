"""Storage discovery, metadata catalog and virtual collections."""

from .catalog import FileCatalog
from .collections import VirtualCollectionStore
from .contracts import (
    CatalogEntry,
    CatalogScanReport,
    CollectionItem,
    CollectionKind,
    FileQuery,
    PermissionLevel,
    VirtualCollection,
    VolumeInfo,
)
from .registry import VolumeRegistry
from .materialize import (
    ApprovalAuthority,
    ApprovalGrant,
    MaterializeItem,
    MaterializePlan,
    MaterializeService,
)

__all__ = [
    "CatalogEntry",
    "CatalogScanReport",
    "ApprovalAuthority",
    "ApprovalGrant",
    "CollectionItem",
    "CollectionKind",
    "FileCatalog",
    "FileQuery",
    "MaterializeItem",
    "MaterializePlan",
    "MaterializeService",
    "PermissionLevel",
    "VirtualCollection",
    "VirtualCollectionStore",
    "VolumeInfo",
    "VolumeRegistry",
]
