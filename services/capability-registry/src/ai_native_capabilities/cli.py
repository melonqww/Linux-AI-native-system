"""JSON CLI for manifest and Capability Registry development."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from enum import Enum
from pathlib import Path

from .manifest import load_manifest, manifest_to_dict
from .registry import CapabilityRegistry


def _json_default(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=_json_default))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI-native Linux Capability Registry")
    parser.add_argument("--database", type=Path, default=Path("data/capabilities/registry.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("manifest", type=Path)

    sync = commands.add_parser("sync")
    sync.add_argument("roots", type=Path, nargs="*", default=[Path("services")])

    commands.add_parser("list")
    show = commands.add_parser("show")
    show.add_argument("module_id")
    commands.add_parser("capabilities")
    commands.add_parser("contracts")
    providers = commands.add_parser("providers")
    providers.add_argument("capability_id")
    providers.add_argument("--all", action="store_true")
    enable = commands.add_parser("enable")
    enable.add_argument("module_id")
    disable = commands.add_parser("disable")
    disable.add_argument("module_id")
    quarantine = commands.add_parser("quarantine")
    quarantine.add_argument("module_id")
    quarantine.add_argument("reason")
    clear = commands.add_parser("clear-quarantine")
    clear.add_argument("module_id")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    if args.command == "validate":
        result: object = manifest_to_dict(load_manifest(args.manifest))
    else:
        registry = CapabilityRegistry(args.database)
        if args.command == "sync":
            result = asdict(registry.sync(args.roots))
        elif args.command == "list":
            result = [asdict(module) for module in registry.list_modules()]
        elif args.command == "show":
            result = asdict(registry.get_module(args.module_id))
        elif args.command == "capabilities":
            result = registry.available_capabilities()
        elif args.command == "contracts":
            result = [asdict(contract) for contract in registry.capability_contracts()]
        elif args.command == "providers":
            result = [
                asdict(provider)
                for provider in registry.providers(args.capability_id, available_only=not args.all)
            ]
        elif args.command == "enable":
            result = asdict(registry.set_enabled(args.module_id, True))
        elif args.command == "disable":
            result = asdict(registry.set_enabled(args.module_id, False))
        elif args.command == "quarantine":
            result = asdict(registry.quarantine(args.module_id, args.reason))
        else:
            result = asdict(registry.clear_quarantine(args.module_id))
    _print(result)
    return 0
