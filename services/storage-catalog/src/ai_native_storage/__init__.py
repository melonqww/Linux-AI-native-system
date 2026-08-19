"""Storage discovery, metadata catalog and virtual collections."""

from .catalog import FileCatalog
from .collections import VirtualCollectionStore
from .contracts import (
    CatalogEntry,
    CatalogScanReport,
    CollectionItem,
    CollectionKind,
    EntryType,
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
    MaterializeDestinationError,
    MaterializeError,
    MaterializeIntegrityError,
    MaterializePlan,
    MaterializeRollbackError,
    MaterializeService,
    MaterializeSpaceError,
)

__all__ = [
    "CatalogEntry",
    "CatalogScanReport",
    "ApprovalAuthority",
    "ApprovalGrant",
    "CollectionItem",
    "CollectionKind",
    "EntryType",
    "FileCatalog",
    "FileQuery",
    "MaterializeItem",
    "MaterializeDestinationError",
    "MaterializeError",
    "MaterializeIntegrityError",
    "MaterializePlan",
    "MaterializeRollbackError",
    "MaterializeService",
    "MaterializeSpaceError",
    "PermissionLevel",
    "VirtualCollection",
    "VirtualCollectionStore",
    "VolumeInfo",
    "VolumeRegistry",
]
