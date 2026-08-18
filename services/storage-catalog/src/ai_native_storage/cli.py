"""JSON command-line interface for storage-catalog development."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from enum import Enum
from pathlib import Path

from .catalog import FileCatalog
from .collections import VirtualCollectionStore
from .contracts import FileQuery, PermissionLevel
from .registry import VolumeRegistry


def _json_default(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=_json_default))


def _add_query_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", action="append", default=[], help="file name fragment")
    parser.add_argument("--extension", action="append", default=[], help="extension, for example pdf")
    parser.add_argument("--role", action="append", default=[])
    parser.add_argument("--volume", action="append", default=[])
    parser.add_argument("--limit", type=int, default=100)


def _query_from_args(args: argparse.Namespace) -> FileQuery:
    return FileQuery(
        name_contains=tuple(args.name),
        extensions=tuple(args.extension),
        roles=tuple(args.role),
        volume_ids=tuple(args.volume),
        limit=args.limit,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI-native Linux storage catalog")
    parser.add_argument("--database", type=Path, default=Path("data/storage/catalog.sqlite3"))
    components = parser.add_subparsers(dest="component", required=True)

    volumes = components.add_parser("volumes")
    volume_commands = volumes.add_subparsers(dest="action", required=True)
    volume_commands.add_parser("refresh")
    list_volumes = volume_commands.add_parser("list")
    list_volumes.add_argument("--available-only", action="store_true")
    allow_volume = volume_commands.add_parser("permission")
    allow_volume.add_argument("volume_id")
    allow_volume.add_argument("level", choices=[level.value for level in PermissionLevel])

    catalog = components.add_parser("catalog")
    catalog_commands = catalog.add_subparsers(dest="action", required=True)
    scan = catalog_commands.add_parser("scan")
    scan.add_argument("volume_id")
    search = catalog_commands.add_parser("search")
    _add_query_arguments(search)
    catalog_commands.add_parser("status")

    collections = components.add_parser("collections")
    collection_commands = collections.add_subparsers(dest="action", required=True)
    collection_commands.add_parser("list")
    show = collection_commands.add_parser("show")
    show.add_argument("collection_id")
    smart = collection_commands.add_parser("create-smart")
    smart.add_argument("title")
    _add_query_arguments(smart)
    snapshot = collection_commands.add_parser("create-snapshot")
    snapshot.add_argument("title")
    _add_query_arguments(snapshot)
    delete = collection_commands.add_parser("delete")
    delete.add_argument("collection_id")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()

    if args.component == "volumes":
        registry = VolumeRegistry(args.database)
        if args.action == "refresh":
            result: object = [asdict(volume) for volume in registry.refresh()]
        elif args.action == "list":
            result = [asdict(volume) for volume in registry.list_volumes(available_only=args.available_only)]
        else:
            result = asdict(registry.set_permission(args.volume_id, PermissionLevel(args.level)))
    elif args.component == "catalog":
        catalog = FileCatalog(args.database)
        if args.action == "scan":
            result = asdict(catalog.scan_volume(args.volume_id))
        elif args.action == "search":
            result = [asdict(entry) for entry in catalog.search(_query_from_args(args))]
        else:
            result = catalog.status()
    else:
        collections = VirtualCollectionStore(args.database)
        if args.action == "list":
            result = [asdict(collection) for collection in collections.list_collections()]
        elif args.action == "show":
            result = {
                "collection": asdict(collections.get_collection(args.collection_id)),
                "items": [asdict(item) for item in collections.resolve(args.collection_id)],
            }
        elif args.action == "create-smart":
            result = asdict(collections.create_smart(args.title, _query_from_args(args)))
        elif args.action == "create-snapshot":
            query = _query_from_args(args)
            result = asdict(collections.create_snapshot(args.title, collections.catalog.search(query)))
        else:
            collections.delete(args.collection_id)
            result = {"deleted": args.collection_id}

    _print(result)
    return 0
