from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from collections.abc import Callable
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator, Protocol

from .catalog import get_application
from .contracts import BackupRecord, BackendProgress, SnapshotSummary
from .snapd import SnapdError


DEFAULT_AUTOMATIC_RETENTION = timedelta(days=31)


class SnapshotBackend(Protocol):
    def list_snapshots(self, package_name: str | None = None) -> tuple[SnapshotSummary, ...]: ...

    def restore_snapshot(self, set_id: int, package_name: str) -> str: ...

    def progress(self, change_id: str) -> BackendProgress: ...


class BackupStore:
    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self._lock = threading.RLock()
        if self.database.exists() and self.database.is_symlink():
            raise ValueError("software backup database cannot be a symlink")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        columns = ",\n".join(
            f"{field.name} {self._column_type(field.name)}"
            for field in fields(BackupRecord)
        )
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(f"CREATE TABLE IF NOT EXISTS software_backups ({columns})")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS software_backups_package "
                "ON software_backups(package_name)"
            )
        if os.name == "posix":
            os.chmod(self.database, 0o600)

    @staticmethod
    def _column_type(name: str) -> str:
        if name == "backup_id":
            return "TEXT PRIMARY KEY"
        if name in {"schema_version", "set_id", "expiry_estimated"}:
            return "INTEGER NOT NULL"
        if name == "size_bytes":
            return "INTEGER"
        if name in {"expires_at", "restore_change_id", "last_restored_at", "error_code"}:
            return "TEXT"
        return "TEXT NOT NULL"

    def save(self, backup: BackupRecord) -> BackupRecord:
        names = tuple(field.name for field in fields(BackupRecord))
        values = tuple(
            int(value) if name == "expiry_estimated" else value
            for name, value in ((name, getattr(backup, name)) for name in names)
        )
        assignments = ", ".join(f"{name}=excluded.{name}" for name in names[1:])
        placeholders = ", ".join("?" for _ in names)
        with self._lock, self._connect() as connection:
            connection.execute(
                f"INSERT INTO software_backups ({', '.join(names)}) VALUES ({placeholders}) "
                f"ON CONFLICT(backup_id) DO UPDATE SET {assignments}",
                values,
            )
        return backup

    def get(self, backup_id: str) -> BackupRecord:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM software_backups WHERE backup_id = ?", (backup_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown_backup:{backup_id}")
        return self._from_row(row)

    def list(self) -> tuple[BackupRecord, ...]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM software_backups ORDER BY created_at DESC, backup_id DESC"
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> BackupRecord:
        values = {field.name: row[field.name] for field in fields(BackupRecord)}
        values["schema_version"] = int(values["schema_version"])
        values["set_id"] = int(values["set_id"])
        values["expiry_estimated"] = bool(values["expiry_estimated"])
        values["size_bytes"] = None if values["size_bytes"] is None else int(values["size_bytes"])
        return BackupRecord(**values)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()


class BackupManager:
    def __init__(
        self,
        store: BackupStore,
        backend: SnapshotBackend,
        *,
        now_fn: Callable[[], datetime] | None = None,
        automatic_retention: timedelta = DEFAULT_AUTOMATIC_RETENTION,
    ) -> None:
        if automatic_retention <= timedelta(0):
            raise ValueError("automatic_retention must be positive")
        self.store = store
        self.backend = backend
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self.automatic_retention = automatic_retention

    def sync(self, package_name: str | None = None) -> tuple[BackupRecord, ...]:
        seen: set[str] = set()
        for snapshot in self.backend.list_snapshots(package_name):
            try:
                application = get_application(snapshot.package_name)
            except KeyError:
                continue
            backup_id = f"snap:{snapshot.set_id}:{snapshot.package_name}"
            seen.add(backup_id)
            try:
                current = self.store.get(backup_id)
            except KeyError:
                current = None
            created = self._parse_timestamp(snapshot.created_at)
            expires = (
                (created + self.automatic_retention).isoformat()
                if snapshot.automatic
                else None
            )
            record = BackupRecord(
                schema_version=1,
                backup_id=backup_id,
                application_id=application.application_id,
                display_name=application.display_name,
                provider="snap",
                package_name=snapshot.package_name,
                set_id=snapshot.set_id,
                created_at=created.isoformat(),
                expires_at=expires,
                expiry_estimated=snapshot.automatic,
                size_bytes=snapshot.size_bytes,
                state=(
                    current.state
                    if current is not None
                    and current.state in {"restoring", "awaiting_restore_confirmation"}
                    else "available"
                ),
                restore_change_id=None if current is None else current.restore_change_id,
                last_restored_at=None if current is None else current.last_restored_at,
                error_code=None if current is None else current.error_code,
            )
            self.store.save(record)
        for record in self.store.list():
            if package_name is not None and record.package_name != package_name:
                continue
            if record.backup_id not in seen and record.state != "restoring":
                expired = record.remaining_seconds(self._now()) == 0
                self.store.save(
                    replace(record, state="expired" if expired else "unavailable")
                )
        return self.list()

    def prepare_restore(self, backup_id: str) -> BackupRecord:
        backup = self.store.get(backup_id)
        if backup.state != "available":
            raise ValueError("backup_not_available")
        return self.store.save(
            replace(backup, state="awaiting_restore_confirmation", error_code=None)
        )

    def confirm_restore(self, backup_id: str) -> BackupRecord:
        backup = self.store.get(backup_id)
        if backup.state != "awaiting_restore_confirmation":
            raise ValueError("backup_not_awaiting_restore_confirmation")
        try:
            change_id = self.backend.restore_snapshot(backup.set_id, backup.package_name)
        except SnapdError as error:
            return self.store.save(replace(backup, state="available", error_code=error.code))
        return self.store.save(
            replace(backup, state="restoring", restore_change_id=change_id, error_code=None)
        )

    def refresh_restore(self, backup_id: str) -> BackupRecord:
        backup = self.store.get(backup_id)
        if backup.state != "restoring" or not backup.restore_change_id:
            return backup
        try:
            progress = self.backend.progress(backup.restore_change_id)
        except SnapdError as error:
            return self.store.save(replace(backup, error_code=error.code))
        if progress.state == "completed":
            return self.store.save(
                replace(
                    backup,
                    state="available",
                    last_restored_at=self._now().isoformat(),
                    restore_change_id=None,
                    error_code=None,
                )
            )
        if progress.state in {"failed", "canceled", "interrupted"}:
            return self.store.save(
                replace(
                    backup,
                    state="available",
                    restore_change_id=None,
                    error_code=progress.error_code or "snapshot_restore_failed",
                )
            )
        return backup

    def list(self) -> tuple[BackupRecord, ...]:
        return self.store.list()

    def current_time(self) -> datetime:
        return self._now()

    def _now(self) -> datetime:
        value = self._now_fn()
        if value.tzinfo is None:
            raise ValueError("clock must return timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise SnapdError("invalid_snapshot_timestamp") from error
        if parsed.tzinfo is None:
            raise SnapdError("invalid_snapshot_timestamp")
        return parsed.astimezone(UTC)
