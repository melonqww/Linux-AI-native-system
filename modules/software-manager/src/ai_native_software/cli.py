from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .backups import BackupManager, BackupStore
from .contracts import InstallPreferences, RemovalPreferences
from .connectivity import NetworkManagerMonitor
from .manager import SoftwareManager
from .notifications import LinuxDesktopNotifier
from .providers import SnapdProvider
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
    commands.add_parser("dispatch-notifications")
    commands.add_parser("reconcile")

    install = commands.add_parser("prepare-install")
    install.add_argument("application_id")
    install.add_argument("--locale", default="system")
    install.add_argument("--install-location", default="default")
    install.add_argument("--option", action="append", default=[])
    remove = commands.add_parser("prepare-remove")
    remove.add_argument("application_id")
    remove.add_argument("--no-backup", action="store_true")
    commands.add_parser("backups")
    sync_backups = commands.add_parser("sync-backups")
    sync_backups.add_argument("--package-name")
    for name in (
        "confirm",
        "confirm-remove",
        "status",
        "refresh",
        "pause",
        "resume",
        "cancel",
        "ack-notification",
    ):
        command = commands.add_parser(name)
        command.add_argument("task_id")
    for name in ("prepare-restore", "confirm-restore", "refresh-restore"):
        command = commands.add_parser(name)
        command.add_argument("backup_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    arguments = build_parser().parse_args(argv)
    backend = SnapdProvider()
    backups = BackupManager(BackupStore(arguments.database), backend)
    manager = SoftwareManager(
        SoftwareTaskStore(arguments.database),
        backend,
        on_remove_completed=backups.sync,
        notifier=LinuxDesktopNotifier(),
        connectivity=NetworkManagerMonitor(),
    )
    command = arguments.command
    if command == "catalog":
        result: object = [application.to_dict() for application in manager.catalog()]
    elif command == "tasks":
        result = [task.to_dict() for task in manager.list_tasks()]
    elif command == "dispatch-notifications":
        result = [task.to_dict() for task in manager.dispatch_notifications()]
    elif command == "reconcile":
        result = [task.to_dict() for task in manager.reconcile()]
    elif command == "prepare-install":
        result = manager.prepare_install(
            arguments.application_id,
            InstallPreferences(
                locale=arguments.locale,
                install_location=arguments.install_location,
                selected_options=tuple(arguments.option),
            ),
        ).to_dict()
    elif command == "prepare-remove":
        result = manager.prepare_remove(
            arguments.application_id,
            RemovalPreferences(create_backup=not arguments.no_backup),
        ).to_dict()
    elif command == "confirm":
        result = manager.confirm(arguments.task_id).to_dict()
    elif command == "confirm-remove":
        result = manager.confirm_removal(arguments.task_id).to_dict()
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
    elif command == "ack-notification":
        result = manager.acknowledge_notification(arguments.task_id).to_dict()
    elif command == "backups":
        result = [backup.to_dict(now=backups.current_time()) for backup in backups.list()]
    elif command == "sync-backups":
        result = [
            backup.to_dict(now=backups.current_time())
            for backup in backups.sync(arguments.package_name)
        ]
    elif command == "prepare-restore":
        result = backups.prepare_restore(arguments.backup_id).to_dict(
            now=backups.current_time()
        )
    elif command == "confirm-restore":
        result = backups.confirm_restore(arguments.backup_id).to_dict(
            now=backups.current_time()
        )
    elif command == "refresh-restore":
        result = backups.refresh_restore(arguments.backup_id).to_dict(
            now=backups.current_time()
        )
    else:  # pragma: no cover - argparse closes this path
        raise RuntimeError("unknown_command")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
