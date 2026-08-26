from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .manager import SoftwareManager
from .snapd import SnapdClient
from .store import SoftwareTaskStore


def default_database() -> Path:
    configured = os.environ.get("AI_NATIVE_SOFTWARE_DATABASE")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "ai-native-linux" / "software-tasks.sqlite3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-native-software")
    parser.add_argument("--database", type=Path, default=default_database())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("catalog")
    commands.add_parser("tasks")

    for name in ("prepare-install", "prepare-remove"):
        command = commands.add_parser(name)
        command.add_argument("application_id")
    for name in ("confirm", "status", "refresh", "pause", "resume", "cancel"):
        command = commands.add_parser(name)
        command.add_argument("task_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    arguments = build_parser().parse_args(argv)
    manager = SoftwareManager(
        SoftwareTaskStore(arguments.database),
        SnapdClient(),
    )
    command = arguments.command
    if command == "catalog":
        result: object = [application.to_dict() for application in manager.catalog()]
    elif command == "tasks":
        result = [task.to_dict() for task in manager.list_tasks()]
    elif command == "prepare-install":
        result = manager.prepare_install(arguments.application_id).to_dict()
    elif command == "prepare-remove":
        result = manager.prepare_remove(arguments.application_id).to_dict()
    elif command == "confirm":
        result = manager.confirm(arguments.task_id).to_dict()
    elif command == "status":
        result = manager.get(arguments.task_id).to_dict()
    elif command == "refresh":
        result = manager.refresh(arguments.task_id).to_dict()
    elif command == "pause":
        result = manager.pause(arguments.task_id).to_dict()
    elif command == "resume":
        result = manager.resume(arguments.task_id).to_dict()
    elif command == "cancel":
        result = manager.cancel(arguments.task_id).to_dict()
    else:  # pragma: no cover - argparse closes this path
        raise RuntimeError("unknown_command")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
