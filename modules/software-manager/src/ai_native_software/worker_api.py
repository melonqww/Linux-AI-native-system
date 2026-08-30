"""Isolated module-process adapter for the software manager.

The first runtime slice is deliberately read-only. Mutating Snap operations will
be added behind the authenticated runtime confirmation contract separately.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from .backups import BackupManager, BackupStore
from .connectivity import NetworkManagerMonitor
from .manager import SoftwareManager
from .contracts import InstallPreferences, RemovalPreferences
from .notifications import LinuxDesktopNotifier
from .providers import SnapdProvider
from .recovery import RecoveryWorker
from .store import SoftwareTaskStore


_manager: SoftwareManager | None = None
_backups: BackupManager | None = None
_stop_event: threading.Event | None = None
_recovery_thread: threading.Thread | None = None


def worker_start() -> None:
    global _manager, _backups, _stop_event, _recovery_thread
    if _manager is not None:
        raise RuntimeError("software_manager_already_started")
    database = Path(
        os.environ.get(
            "AI_NATIVE_SOFTWARE_DATABASE",
            Path.cwd() / "data" / "software-manager.sqlite3",
        )
    )
    provider = SnapdProvider()
    backup_store = BackupStore(database.with_name(f"{database.stem}-backups.sqlite3"))
    _backups = BackupManager(backup_store, provider)
    _manager = SoftwareManager(
        SoftwareTaskStore(database),
        provider,
        connectivity=NetworkManagerMonitor(),
        notifier=LinuxDesktopNotifier(),
        on_remove_completed=lambda package: _backups.sync(package),
    )
    # Recovery starts only after the parent runtime has acquired its unique
    # Unix socket. A losing second runtime instance must remain read-only.
    _stop_event = None
    _recovery_thread = None


def worker_health() -> dict[str, object]:
    manager = _require_manager()
    return {
        "status": "ready",
        "provider": "snap",
        "platform_supported": sys.platform.startswith("linux"),
        "task_count": len(manager.list_tasks()),
        "recovery_active": _recovery_thread is not None and _recovery_thread.is_alive(),
    }


def worker_invoke(operation: str, payload: dict[str, object]) -> dict[str, object]:
    if operation == "activate":
        if payload:
            raise ValueError("invalid_payload")
        _start_recovery()
        return {"schema_version": 1, "recovery_active": True}
    if operation != "snapshot":
        if operation == "prepare":
            return _prepare(payload)
        if operation == "respond":
            return _respond(payload)
        if operation == "control":
            return _control(payload)
        raise ValueError("unknown_operation")
    if payload:
        raise ValueError("invalid_payload")
    return _snapshot()


def worker_stop() -> None:
    global _manager, _backups, _stop_event, _recovery_thread
    if _stop_event is not None:
        _stop_event.set()
    if _recovery_thread is not None:
        _recovery_thread.join(timeout=3)
    _manager = None
    _backups = None
    _stop_event = None
    _recovery_thread = None


def _require_manager() -> SoftwareManager:
    if _manager is None:
        raise RuntimeError("software_manager_not_started")
    return _manager


def _require_backups() -> BackupManager:
    if _backups is None:
        raise RuntimeError("software_manager_not_started")
    return _backups


def _start_recovery() -> None:
    global _stop_event, _recovery_thread
    if _recovery_thread is not None and _recovery_thread.is_alive():
        return
    manager = _require_manager()
    _stop_event = threading.Event()
    _recovery_thread = threading.Thread(
        target=RecoveryWorker(manager).run_forever,
        args=(_stop_event,),
        name="software-manager-recovery",
        daemon=True,
    )
    _recovery_thread.start()


def _snapshot() -> dict[str, object]:
    manager = _require_manager()
    backups = _require_backups()
    return {
        "schema_version": 1,
        "provider": "snap",
        "platform_supported": sys.platform.startswith("linux"),
        "catalog": [application.to_dict() for application in manager.catalog()],
        "tasks": [task.to_dict() for task in manager.list_tasks()],
        "backups": [backup.to_dict(now=backups.current_time()) for backup in backups.list()],
    }


def _prepare(payload: dict[str, object]) -> dict[str, object]:
    action = payload.get("action")
    application_id = payload.get("application_id")
    if not isinstance(application_id, str) or not application_id:
        raise ValueError("invalid_application_id")
    manager = _require_manager()
    if action == "install":
        if set(payload) != {
            "action", "application_id", "locale", "install_location", "selected_options"
        }:
            raise ValueError("invalid_payload")
        selected_options = payload.get("selected_options")
        if not isinstance(selected_options, list) or any(
            not isinstance(item, str) for item in selected_options
        ):
            raise ValueError("invalid_selected_options")
        task = manager.prepare_install(
            application_id,
            InstallPreferences(
                locale=_required_string(payload, "locale"),
                install_location=_required_string(payload, "install_location"),
                selected_options=tuple(selected_options),
            ),
        )
    elif action == "remove":
        if set(payload) != {"action", "application_id", "create_backup"}:
            raise ValueError("invalid_payload")
        create_backup = payload.get("create_backup")
        if not isinstance(create_backup, bool):
            raise ValueError("invalid_create_backup")
        task = manager.prepare_remove(
            application_id, RemovalPreferences(create_backup=create_backup)
        )
    else:
        raise ValueError("invalid_action")
    return {"schema_version": 1, "task": task.to_dict()}


def _respond(payload: dict[str, object]) -> dict[str, object]:
    if set(payload) != {"task_id", "confirmed", "action", "final_confirmation"}:
        raise ValueError("invalid_payload")
    confirmed = payload.get("confirmed")
    if not isinstance(confirmed, bool):
        raise ValueError("invalid_confirmation")
    manager = _require_manager()
    task = manager.get(_required_string(payload, "task_id"))
    action = payload.get("action")
    final_confirmation = payload.get("final_confirmation")
    if action != task.action or not isinstance(final_confirmation, bool):
        raise ValueError("task_action_mismatch")
    if final_confirmation != task.requires_final_confirmation:
        raise ValueError("confirmation_stage_mismatch")
    if not confirmed:
        task = manager.cancel(task.task_id)
    elif task.state == "awaiting_confirmation":
        task = manager.confirm(task.task_id)
    elif task.action == "remove" and task.state == "awaiting_final_confirmation":
        task = manager.confirm_removal(task.task_id)
    else:
        raise ValueError("task_not_awaiting_confirmation")
    return {"schema_version": 1, "task": task.to_dict()}


def _control(payload: dict[str, object]) -> dict[str, object]:
    if set(payload) != {"task_id", "action"}:
        raise ValueError("invalid_payload")
    manager = _require_manager()
    task_id = _required_string(payload, "task_id")
    action = payload.get("action")
    operations = {
        "pause": manager.pause,
        "resume": manager.resume,
        "cancel": manager.cancel,
    }
    if action not in operations:
        raise ValueError("invalid_control_action")
    task = operations[action](task_id)
    return {"schema_version": 1, "task": task.to_dict()}


def _required_string(payload: dict[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(f"invalid_{field}")
    return value
