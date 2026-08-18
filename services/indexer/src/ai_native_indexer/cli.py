"""JSON CLI used for development and later runtime integration."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .file_policy import FilePolicy
from .service import IndexerService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI-native Linux local indexer")
    parser.add_argument("--database", type=Path, default=Path("data/index/index.sqlite3"))
    parser.add_argument(
        "--max-file-mb",
        type=int,
        default=20,
        help="maximum text file size to index (default: 20 MiB)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    index_command = commands.add_parser("index", help="index one explicitly selected directory")
    index_command.add_argument("path", type=Path)

    search_command = commands.add_parser("search", help="search indexed text")
    search_command.add_argument("query")
    search_command.add_argument("--limit", type=int, default=5)

    commands.add_parser("status", help="show index statistics")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    if not 1 <= args.max_file_mb <= 1_024:
        raise SystemExit("--max-file-mb must be from 1 to 1024")
    service = IndexerService(
        args.database,
        file_policy=FilePolicy(max_file_bytes=args.max_file_mb * 1_048_576),
    )
    if args.command == "index":
        result: object = asdict(service.index_directory(args.path))
    elif args.command == "search":
        result = [asdict(hit) for hit in service.search(args.query, limit=args.limit)]
    else:
        result = service.get_index_status()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
