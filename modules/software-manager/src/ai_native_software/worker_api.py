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
_desktop_ids: dict[str, tuple[str, ...]] = {}


def worker_start() -> None:
    global _manager, _backups, _stop_event, _recovery_thread, _desktop_ids
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
    _desktop_ids = {}


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
        if operation == "restore":
            return _restore(payload)
        raise ValueError("unknown_operation")
    if payload:
        raise ValueError("invalid_payload")
    return _snapshot()


def worker_stop() -> None:
    global _manager, _backups, _stop_event, _recovery_thread, _desktop_ids
    if _stop_event is not None:
        _stop_event.set()
    if _recovery_thread is not None:
        _recovery_thread.join(timeout=3)
    _manager = None
    _backups = None
    _stop_event = None
    _recovery_thread = None
    _desktop_ids = {}


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
    _reconcile_backups(manager, backups)
    tasks = manager.list_tasks()
    backup_records = backups.list()
    restore_by_task = {
        backup.restore_task_id: backup
        for backup in backup_records
        if backup.restore_task_id is not None
    }
    latest: dict[str, object] = {}
    for task in tasks:
        latest.setdefault(task.application_id, task)
    catalog: list[dict[str, object]] = []
    desktop_lookup = getattr(manager.backend, "desktop_ids", None)
    for application in manager.catalog():
        payload = application.to_dict()
        task = latest.get(application.application_id)
        installed = bool(
            task is not None
            and getattr(task, "action", None) == "install"
            and getattr(task, "state", None) == "completed"
        )
        if installed and callable(desktop_lookup):
            if application.application_id not in _desktop_ids:
                try:
                    discovered = tuple(
                        desktop_lookup(application.package_name)
                    )
                    if discovered:
                        _desktop_ids[application.application_id] = discovered
                except Exception:
                    pass
            payload["desktop_ids"] = list(
                _desktop_ids.get(application.application_id, ())
            )
        else:
            _desktop_ids.pop(application.application_id, None)
            payload["desktop_ids"] = []
        catalog.append(payload)
    return {
        "schema_version": 1,
        "provider": "snap",
        "platform_supported": sys.platform.startswith("linux"),
        "catalog": catalog,
        "tasks": [
            {
                **task.to_dict(),
                **(
                    {
                        "install_source": "backup",
                        "restore_state": restore_by_task[task.task_id].state,
                    }
                    if task.task_id in restore_by_task
                    else {}
                ),
            }
            for task in tasks
        ],
        "backups": [
            backup.to_dict(now=backups.current_time()) for backup in backup_records
        ],
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


def _restore(payload: dict[str, object]) -> dict[str, object]:
    if set(payload) != {"backup_id"}:
        raise ValueError("invalid_payload")
    backup_id = _required_string(payload, "backup_id")
    manager = _require_manager()
    backups = _require_backups()
    prepared = backups.prepare_restore(backup_id)
    try:
        task = manager.prepare_install(
            prepared.application_id,
            InstallPreferences(
                selected_options=("pin_to_gnome", "restore_from_backup"),
            ),
        )
        task = manager.confirm(task.task_id)
        backup = backups.begin_reinstall(backup_id, task.task_id)
    except Exception:
        backups.cancel_restore_preparation(backup_id, "restore_reinstall_start_failed")
        raise
    return {
        "schema_version": 1,
        "task": task.to_dict(),
        "backup": backup.to_dict(now=backups.current_time()),
    }


def _reconcile_backups(manager: SoftwareManager, backups: BackupManager) -> None:
    for backup in backups.list():
        if backup.state == "reinstalling" and backup.restore_task_id:
            try:
                task = manager.get(backup.restore_task_id)
            except KeyError:
                backups.continue_reinstall(backup.backup_id, "failed")
                continue
            backups.continue_reinstall(backup.backup_id, task.state)
        elif backup.state == "restoring":
            backups.refresh_restore(backup.backup_id)


def _required_string(payload: dict[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(f"invalid_{field}")
    return value
