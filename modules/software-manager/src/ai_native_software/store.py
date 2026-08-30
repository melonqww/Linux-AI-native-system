from __future__ import annotations

import os
import json
import sqlite3
import threading
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from .contracts import InstallPreferences, RemovalPreferences, SoftwareTask


class SoftwareTaskStore:
    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self._lock = threading.RLock()
        if self.database.exists() and self.database.is_symlink():
            raise ValueError("software task database cannot be a symlink")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS software_tasks (
                    task_id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    application_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    package_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    preferences_json TEXT NOT NULL,
                    removal_preferences_json TEXT NOT NULL DEFAULT '{"create_backup":true}',
                    state TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    progress_percent INTEGER,
                    downloaded_bytes INTEGER,
                    total_bytes INTEGER,
                    download_speed_bps INTEGER,
                    eta_seconds INTEGER,
                    completion_notification_pending INTEGER NOT NULL DEFAULT 0,
                    pause_reason TEXT,
                    auto_resume INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TEXT,
                    last_progress_at TEXT,
                    external_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_code TEXT
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS software_tasks_state ON software_tasks(state)"
            )
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(software_tasks)")
            }
            if "removal_preferences_json" not in columns:
                connection.execute(
                    "ALTER TABLE software_tasks ADD COLUMN removal_preferences_json "
                    "TEXT NOT NULL DEFAULT '{\"create_backup\":true}'"
                )
            migrations = {
                "downloaded_bytes": "INTEGER",
                "total_bytes": "INTEGER",
                "download_speed_bps": "INTEGER",
                "eta_seconds": "INTEGER",
                "completion_notification_pending": "INTEGER NOT NULL DEFAULT 0",
                "pause_reason": "TEXT",
                "auto_resume": "INTEGER NOT NULL DEFAULT 0",
                "retry_count": "INTEGER NOT NULL DEFAULT 0",
                "next_retry_at": "TEXT",
                "last_progress_at": "TEXT",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE software_tasks ADD COLUMN {name} {definition}"
                    )
            connection.execute("PRAGMA user_version = 4")
        if os.name == "posix":
            os.chmod(self.database, 0o600)

    def save(self, task: SoftwareTask) -> SoftwareTask:
        values = (
            task.task_id,
            task.schema_version,
            task.application_id,
            task.display_name,
            task.provider,
            task.package_name,
            task.action,
            json.dumps(task.preferences.to_dict(), ensure_ascii=False, separators=(",", ":")),
            json.dumps(
                task.removal_preferences.to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            task.state,
            task.phase,
            task.progress_percent,
            task.downloaded_bytes,
            task.total_bytes,
            task.download_speed_bps,
            task.eta_seconds,
            int(task.completion_notification_pending),
            task.pause_reason,
            int(task.auto_resume),
            task.retry_count,
            task.next_retry_at,
            task.last_progress_at,
            task.external_id,
            task.created_at,
            task.updated_at,
            task.error_code,
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO software_tasks(
                    task_id, schema_version, application_id, display_name,
                    provider, package_name, action, preferences_json,
                    removal_preferences_json, state, phase, progress_percent,
                    downloaded_bytes, total_bytes, download_speed_bps, eta_seconds,
                    completion_notification_pending, pause_reason, auto_resume,
                    retry_count, next_retry_at, last_progress_at, external_id, created_at,
                    updated_at, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    preferences_json = excluded.preferences_json,
                    removal_preferences_json = excluded.removal_preferences_json,
                    state = excluded.state,
                    phase = excluded.phase,
                    progress_percent = excluded.progress_percent,
                    downloaded_bytes = excluded.downloaded_bytes,
                    total_bytes = excluded.total_bytes,
                    download_speed_bps = excluded.download_speed_bps,
                    eta_seconds = excluded.eta_seconds,
                    completion_notification_pending = excluded.completion_notification_pending,
                    pause_reason = excluded.pause_reason,
                    auto_resume = excluded.auto_resume,
                    retry_count = excluded.retry_count,
                    next_retry_at = excluded.next_retry_at,
                    last_progress_at = excluded.last_progress_at,
                    external_id = excluded.external_id,
                    updated_at = excluded.updated_at,
                    error_code = excluded.error_code""",
                values,
            )
        return task

    def get(self, task_id: str) -> SoftwareTask:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM software_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown_task:{task_id}")
        return self._from_row(row)

    def list(self) -> tuple[SoftwareTask, ...]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM software_tasks ORDER BY created_at DESC, task_id DESC"
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> SoftwareTask:
        try:
            preferences_payload = json.loads(str(row["preferences_json"]))
            preferences = InstallPreferences(
                locale=str(preferences_payload["locale"]),
                install_location=str(preferences_payload["install_location"]),
                selected_options=tuple(str(item) for item in preferences_payload["selected_options"]),
            )
            removal_payload = json.loads(str(row["removal_preferences_json"]))
            create_backup = removal_payload["create_backup"]
            if not isinstance(create_backup, bool):
                raise ValueError("invalid create_backup")
            removal_preferences = RemovalPreferences(create_backup=create_backup)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("invalid_stored_install_preferences") from error
        return SoftwareTask(
            schema_version=int(row["schema_version"]),
            task_id=str(row["task_id"]),
            application_id=str(row["application_id"]),
            display_name=str(row["display_name"]),
            provider=str(row["provider"]),
            package_name=str(row["package_name"]),
            action=str(row["action"]),
            preferences=preferences,
            removal_preferences=removal_preferences,
            state=str(row["state"]),
            phase=str(row["phase"]),
            progress_percent=(
                None if row["progress_percent"] is None else int(row["progress_percent"])
            ),
            downloaded_bytes=(
                None if row["downloaded_bytes"] is None else int(row["downloaded_bytes"])
            ),
            total_bytes=None if row["total_bytes"] is None else int(row["total_bytes"]),
            download_speed_bps=(
                None if row["download_speed_bps"] is None else int(row["download_speed_bps"])
            ),
            eta_seconds=None if row["eta_seconds"] is None else int(row["eta_seconds"]),
            completion_notification_pending=bool(row["completion_notification_pending"]),
            pause_reason=None if row["pause_reason"] is None else str(row["pause_reason"]),
            auto_resume=bool(row["auto_resume"]),
            retry_count=int(row["retry_count"]),
            next_retry_at=None if row["next_retry_at"] is None else str(row["next_retry_at"]),
            last_progress_at=(
                None if row["last_progress_at"] is None else str(row["last_progress_at"])
            ),
            external_id=None if row["external_id"] is None else str(row["external_id"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            error_code=None if row["error_code"] is None else str(row["error_code"]),
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()
