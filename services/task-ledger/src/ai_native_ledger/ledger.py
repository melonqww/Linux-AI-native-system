"""Durable, user-facing task history. This is deliberately not a debug log."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

from .contracts import (
    CancellationRequested,
    InvalidTransitionError,
    ItemOutcome,
    ObjectReference,
    ResumePoint,
    TERMINAL_STATES,
    TaskNotFoundError,
    TaskState,
    TaskView,
)


_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    activity TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    total_items INTEGER,
    processed_count INTEGER NOT NULL DEFAULT 0,
    succeeded_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    resumable INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    continue_requested INTEGER NOT NULL DEFAULT 0,
    checkpoint_version INTEGER,
    checkpoint_json TEXT
);
CREATE TABLE IF NOT EXISTS task_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    event TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS task_events_by_task
    ON task_events(task_id, sequence);
CREATE TABLE IF NOT EXISTS task_references (
    reference_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    display_name TEXT NOT NULL,
    locator TEXT NOT NULL,
    outcome TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS task_references_by_task
    ON task_references(task_id, created_at);
PRAGMA user_version = 1;
"""


class TaskLedger:
    """SQLite-backed task state with a deliberately small user-facing projection."""

    def __init__(self, database: Path, *, retention_days: int = 7) -> None:
        if retention_days < 1 or retention_days > 365:
            raise ValueError("retention_days must be between 1 and 365")
        self.database = Path(database)
        self.retention_days = retention_days
        self._lock = RLock()
        if self.database.exists() and self.database.is_symlink():
            raise ValueError("task ledger database cannot be a symlink")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(_SCHEMA)
        if os.name == "posix":
            os.chmod(self.database, 0o600)

    def create(
        self,
        activity: str,
        *,
        task_id: str | None = None,
        total_items: int | None = None,
        resumable: bool = False,
    ) -> TaskView:
        identifier = task_id or str(uuid4())
        self._validate_uuid(identifier)
        self._validate_token(activity, "activity")
        if total_items is not None and total_items < 0:
            raise ValueError("total_items cannot be negative")
        now = self._now()
        with self._transaction() as connection:
            try:
                connection.execute(
                    """INSERT INTO tasks(
                        task_id, activity, state, created_at, updated_at,
                        total_items, resumable
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        identifier,
                        activity,
                        TaskState.PLANNED.value,
                        now,
                        now,
                        total_items,
                        int(resumable),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise InvalidTransitionError("task_already_exists") from error
            self._event(connection, identifier, "created", now)
        return self.get(identifier)

    def start(self, task_id: str) -> TaskView:
        self._transition(task_id, {TaskState.PLANNED}, TaskState.RUNNING, "started")
        return self.get(task_id)

    def awaiting_approval(self, task_id: str) -> TaskView:
        self._transition(
            task_id,
            {TaskState.RUNNING},
            TaskState.AWAITING_APPROVAL,
            "awaiting_approval",
        )
        return self.get(task_id)

    def approval_received(self, task_id: str) -> TaskView:
        self._transition(
            task_id,
            {TaskState.AWAITING_APPROVAL},
            TaskState.RUNNING,
            "approval_received",
        )
        return self.get(task_id)

    def record_item(
        self,
        task_id: str,
        *,
        outcome: ItemOutcome,
        locator: Path | str,
        display_name: str | None = None,
        kind: str = "file",
    ) -> ObjectReference:
        if not isinstance(outcome, ItemOutcome):
            raise ValueError("outcome must be a supported item outcome")
        self._validate_token(kind, "kind")
        locator_value = self._validated_locator(locator)
        name = display_name or self._locator_name(locator_value)
        self._validate_label(name, "display_name")
        reference_id = str(uuid4())
        now = self._now()
        with self._transaction() as connection:
            row = self._task_row(connection, task_id)
            if TaskState(row["state"]) is not TaskState.RUNNING:
                raise InvalidTransitionError("task_not_running")
            total = row["total_items"]
            if total is not None and row["processed_count"] >= total:
                raise InvalidTransitionError("total_items_exceeded")
            succeeded = int(outcome is ItemOutcome.SUCCEEDED)
            skipped = int(outcome is ItemOutcome.SKIPPED)
            connection.execute(
                """UPDATE tasks SET
                    processed_count = processed_count + 1,
                    succeeded_count = succeeded_count + ?,
                    skipped_count = skipped_count + ?, updated_at = ?
                    WHERE task_id = ?""",
                (succeeded, skipped, now, task_id),
            )
            connection.execute(
                """INSERT INTO task_references(
                    reference_id, task_id, kind, display_name, locator, outcome, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    reference_id,
                    task_id,
                    kind,
                    name,
                    locator_value,
                    outcome.value,
                    now,
                ),
            )
            self._event(connection, task_id, f"item_{outcome.value}", now)
        return self.reference(reference_id)

    def add_reference(
        self,
        task_id: str,
        *,
        locator: Path | str,
        display_name: str | None = None,
        kind: str = "file",
        outcome: ItemOutcome = ItemOutcome.SUCCEEDED,
    ) -> ObjectReference:
        """Attach a clickable result without changing processing counters."""
        if not isinstance(outcome, ItemOutcome):
            raise ValueError("outcome must be a supported item outcome")
        self._validate_token(kind, "kind")
        locator_value = self._validated_locator(locator)
        name = display_name or self._locator_name(locator_value)
        self._validate_label(name, "display_name")
        reference_id = str(uuid4())
        now = self._now()
        with self._transaction() as connection:
            row = self._task_row(connection, task_id)
            if TaskState(row["state"]) not in {
                TaskState.RUNNING,
                TaskState.AWAITING_APPROVAL,
            }:
                raise InvalidTransitionError("task_not_active")
            connection.execute(
                """INSERT INTO task_references(
                    reference_id, task_id, kind, display_name, locator, outcome, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    reference_id,
                    task_id,
                    kind,
                    name,
                    locator_value,
                    outcome.value,
                    now,
                ),
            )
            connection.execute(
                "UPDATE tasks SET updated_at = ? WHERE task_id = ?", (now, task_id)
            )
            self._event(connection, task_id, "reference_added", now)
        return self.reference(reference_id)

    def save_checkpoint(
        self, task_id: str, checkpoint: Mapping[str, Any], *, version: int = 1
    ) -> None:
        if version < 1:
            raise ValueError("checkpoint version must be positive")
        encoded = self._encode_checkpoint(checkpoint)
        now = self._now()
        with self._transaction() as connection:
            row = self._task_row(connection, task_id)
            if not row["resumable"]:
                raise InvalidTransitionError("task_not_resumable")
            if TaskState(row["state"]) is not TaskState.RUNNING:
                raise InvalidTransitionError("task_not_running")
            connection.execute(
                """UPDATE tasks SET checkpoint_version = ?, checkpoint_json = ?,
                   updated_at = ? WHERE task_id = ?""",
                (version, encoded, now, task_id),
            )
            self._event(connection, task_id, "checkpoint_saved", now)

    def interrupt(self, task_id: str) -> TaskView:
        now = self._now()
        with self._transaction() as connection:
            row = self._task_row(connection, task_id)
            if TaskState(row["state"]) is not TaskState.RUNNING:
                raise InvalidTransitionError("task_not_running")
            if not row["resumable"] or row["checkpoint_json"] is None:
                raise InvalidTransitionError("safe_checkpoint_unavailable")
            connection.execute(
                "UPDATE tasks SET state = ?, updated_at = ? WHERE task_id = ?",
                (TaskState.INTERRUPTED.value, now, task_id),
            )
            self._event(connection, task_id, "interrupted", now)
        return self.get(task_id)

    def request_continue(self, task_id: str) -> TaskView:
        now = self._now()
        with self._transaction() as connection:
            row = self._task_row(connection, task_id)
            if TaskState(row["state"]) is not TaskState.INTERRUPTED:
                raise InvalidTransitionError("task_not_interrupted")
            if row["continue_requested"]:
                return self._view(connection, row, include_references=False)
            connection.execute(
                "UPDATE tasks SET continue_requested = 1, updated_at = ? WHERE task_id = ?",
                (now, task_id),
            )
            self._event(connection, task_id, "continue_requested", now)
        return self.get(task_id)

    def claim_continue(self, task_id: str) -> ResumePoint:
        now = self._now()
        with self._transaction() as connection:
            row = self._task_row(connection, task_id)
            if TaskState(row["state"]) is not TaskState.INTERRUPTED:
                raise InvalidTransitionError("task_not_interrupted")
            if not row["continue_requested"]:
                raise InvalidTransitionError("continue_not_requested")
            if row["checkpoint_json"] is None or row["checkpoint_version"] is None:
                raise InvalidTransitionError("safe_checkpoint_unavailable")
            checkpoint = json.loads(row["checkpoint_json"])
            connection.execute(
                """UPDATE tasks SET state = ?, cancel_requested = 0,
                   continue_requested = 0, updated_at = ?
                   WHERE task_id = ?""",
                (TaskState.RUNNING.value, now, task_id),
            )
            self._event(connection, task_id, "continued", now)
        return ResumePoint(task_id, row["checkpoint_version"], checkpoint)

    def request_cancel(self, task_id: str) -> TaskView:
        now = self._now()
        with self._transaction() as connection:
            row = self._task_row(connection, task_id)
            if TaskState(row["state"]) is not TaskState.RUNNING:
                raise InvalidTransitionError("task_not_running")
            if row["cancel_requested"]:
                return self._view(connection, row, include_references=False)
            connection.execute(
                "UPDATE tasks SET cancel_requested = 1, updated_at = ? WHERE task_id = ?",
                (now, task_id),
            )
            self._event(connection, task_id, "cancel_requested", now)
        return self.get(task_id)

    def raise_if_cancelled(self, task_id: str) -> None:
        with self._connect() as connection:
            row = self._task_row(connection, task_id)
            if row["cancel_requested"]:
                raise CancellationRequested("cancellation_requested")

    def cancelled(self, task_id: str) -> TaskView:
        self._finish(
            task_id,
            {TaskState.RUNNING, TaskState.AWAITING_APPROVAL},
            TaskState.CANCELLED,
            "cancelled",
        )
        return self.get(task_id)

    def complete(self, task_id: str) -> TaskView:
        with self._connect() as connection:
            row = self._task_row(connection, task_id)
            target = (
                TaskState.COMPLETED_WITH_SKIPS
                if row["skipped_count"]
                else TaskState.COMPLETED
            )
        self._finish(task_id, {TaskState.RUNNING}, target, "completed")
        return self.get(task_id)

    def fail(self, task_id: str) -> TaskView:
        self._finish(
            task_id,
            {TaskState.PLANNED, TaskState.RUNNING, TaskState.AWAITING_APPROVAL},
            TaskState.FAILED,
            "failed",
        )
        return self.get(task_id)

    def recover_after_restart(self) -> tuple[str, ...]:
        """Move abandoned work to a safe user-visible state on daemon startup."""
        now = self._now()
        recovered: list[str] = []
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE state IN (?, ?)",
                (TaskState.RUNNING.value, TaskState.AWAITING_APPROVAL.value),
            ).fetchall()
            for row in rows:
                can_resume = bool(row["resumable"] and row["checkpoint_json"])
                state = TaskState.INTERRUPTED if can_resume else TaskState.FAILED
                finished = None if can_resume else now
                connection.execute(
                """UPDATE tasks SET state = ?, updated_at = ?, finished_at = ?,
                       cancel_requested = 0, continue_requested = 0 WHERE task_id = ?""",
                    (state.value, now, finished, row["task_id"]),
                )
                self._event(connection, row["task_id"], "interrupted" if can_resume else "failed", now)
                recovered.append(row["task_id"])
        return tuple(recovered)

    def purge_expired(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        cutoff = (current - timedelta(days=self.retention_days)).isoformat()
        terminal = tuple(state.value for state in TERMINAL_STATES)
        placeholders = ",".join("?" for _ in terminal)
        with self._transaction() as connection:
            cursor = connection.execute(
                f"DELETE FROM tasks WHERE state IN ({placeholders}) AND finished_at < ?",
                (*terminal, cutoff),
            )
            return cursor.rowcount

    def get(self, task_id: str, *, include_references: bool = True) -> TaskView:
        with self._connect() as connection:
            row = self._task_row(connection, task_id)
            return self._view(connection, row, include_references=include_references)

    def list_recent(self, *, limit: int = 200) -> tuple[TaskView, ...]:
        if limit < 1 or limit > 1_000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return tuple(
                self._view(connection, row, include_references=False) for row in rows
            )

    def reference(self, reference_id: str) -> ObjectReference:
        self._validate_uuid(reference_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_references WHERE reference_id = ?", (reference_id,)
            ).fetchone()
            if row is None:
                raise TaskNotFoundError("reference_not_found")
            return self._reference(row)

    def event_names(self, task_id: str) -> tuple[str, ...]:
        """Stable event names for the activity console; no diagnostic payload exists."""
        with self._connect() as connection:
            self._task_row(connection, task_id)
            rows = connection.execute(
                "SELECT event FROM task_events WHERE task_id = ? ORDER BY sequence",
                (task_id,),
            ).fetchall()
            return tuple(row["event"] for row in rows)

    def _transition(self, task_id, allowed, target, event) -> None:
        now = self._now()
        with self._transaction() as connection:
            row = self._task_row(connection, task_id)
            if TaskState(row["state"]) not in allowed:
                raise InvalidTransitionError("transition_not_allowed")
            connection.execute(
                "UPDATE tasks SET state = ?, updated_at = ? WHERE task_id = ?",
                (target.value, now, task_id),
            )
            self._event(connection, task_id, event, now)

    def _finish(self, task_id, allowed, target, event) -> None:
        now = self._now()
        with self._transaction() as connection:
            row = self._task_row(connection, task_id)
            if TaskState(row["state"]) not in allowed:
                raise InvalidTransitionError("transition_not_allowed")
            connection.execute(
                """UPDATE tasks SET state = ?, updated_at = ?, finished_at = ?,
                   cancel_requested = 0, checkpoint_json = NULL,
                   checkpoint_version = NULL WHERE task_id = ?""",
                (target.value, now, now, task_id),
            )
            self._event(connection, task_id, event, now)

    def _view(self, connection, row, *, include_references) -> TaskView:
        state = TaskState(row["state"])
        references: tuple[ObjectReference, ...] = ()
        if include_references:
            values = connection.execute(
                "SELECT * FROM task_references WHERE task_id = ? ORDER BY created_at",
                (row["task_id"],),
            ).fetchall()
            references = tuple(self._reference(value) for value in values)
        return TaskView(
            task_id=row["task_id"],
            activity=row["activity"],
            state=state,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            total_items=row["total_items"],
            processed_count=row["processed_count"],
            succeeded_count=row["succeeded_count"],
            skipped_count=row["skipped_count"],
            cancel_requested=bool(row["cancel_requested"]),
            continue_requested=bool(row["continue_requested"]),
            can_cancel=state is TaskState.RUNNING and not row["cancel_requested"],
            can_continue=state is TaskState.INTERRUPTED and not row["continue_requested"],
            references=references,
        )

    @staticmethod
    def _reference(row) -> ObjectReference:
        locator = Path(row["locator"])
        return ObjectReference(
            row["reference_id"],
            row["kind"],
            row["display_name"],
            os.fspath(locator),
            ItemOutcome(row["outcome"]),
            locator.exists(),
        )

    @staticmethod
    def _task_row(connection, task_id):
        TaskLedger._validate_uuid(task_id)
        row = connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise TaskNotFoundError("task_not_found")
        return row

    @staticmethod
    def _event(connection, task_id, event, occurred_at) -> None:
        connection.execute(
            "INSERT INTO task_events(task_id, event, occurred_at) VALUES (?, ?, ?)",
            (task_id, event, occurred_at),
        )

    @contextmanager
    def _transaction(self):
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @staticmethod
    def _encode_checkpoint(checkpoint: Mapping[str, Any]) -> str:
        if not isinstance(checkpoint, Mapping):
            raise ValueError("checkpoint must be an object")
        TaskLedger._validate_checkpoint_value(checkpoint)
        try:
            encoded = json.dumps(
                dict(checkpoint), ensure_ascii=False, sort_keys=True, allow_nan=False
            )
        except (TypeError, ValueError) as error:
            raise ValueError("checkpoint must contain JSON values") from error
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ValueError("checkpoint is too large")
        return encoded

    @staticmethod
    def _validate_checkpoint_value(value: Any, *, depth: int = 0) -> None:
        if depth > 8:
            raise ValueError("checkpoint nesting is too deep")
        if isinstance(value, Mapping):
            forbidden = {"password", "secret", "token", "api_key", "content", "query", "prompt"}
            for key, item in value.items():
                if not isinstance(key, str) or not key or len(key) > 100:
                    raise ValueError("checkpoint keys must be bounded strings")
                if key.casefold() in forbidden:
                    raise ValueError("checkpoint contains a forbidden sensitive field")
                TaskLedger._validate_checkpoint_value(item, depth=depth + 1)
            return
        if isinstance(value, (list, tuple)):
            if len(value) > 1_000:
                raise ValueError("checkpoint collection is too large")
            for item in value:
                TaskLedger._validate_checkpoint_value(item, depth=depth + 1)
            return
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError("checkpoint contains an unsupported value")

    @staticmethod
    def _validated_locator(value: Path | str) -> str:
        if not isinstance(value, (Path, str)):
            raise ValueError("locator must be a local path")
        encoded = os.fspath(value)
        is_absolute = (
            Path(encoded).is_absolute()
            or PurePosixPath(encoded).is_absolute()
            or PureWindowsPath(encoded).is_absolute()
        )
        if not is_absolute or not encoded or len(encoded) > 4_096 or "\0" in encoded:
            raise ValueError("locator must be a bounded absolute local path")
        return encoded

    @staticmethod
    def _locator_name(locator: str) -> str:
        if PureWindowsPath(locator).is_absolute():
            return PureWindowsPath(locator).name
        return PurePosixPath(locator).name

    @staticmethod
    def _validate_uuid(value: str) -> None:
        try:
            parsed = UUID(value)
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("task_id must be a canonical UUID") from error
        if str(parsed) != value:
            raise ValueError("task_id must be a canonical UUID")

    @staticmethod
    def _validate_token(value: str, name: str) -> None:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 100
            or any(
                not (
                    character.islower()
                    or character.isdigit()
                    or character in ".-_"
                )
                for character in value
            )
        ):
            raise ValueError(f"{name} must be a bounded machine token")

    @staticmethod
    def _validate_label(value: str, name: str) -> None:
        if not isinstance(value, str) or not value or len(value) > 255:
            raise ValueError(f"{name} must be a bounded label")
        if any(ord(character) < 32 for character in value):
            raise ValueError(f"{name} contains control characters")

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()
