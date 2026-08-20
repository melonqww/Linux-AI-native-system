"""SQLite-backed, 24-hour user workspace projection."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from .contracts import (
    MessageKind,
    MessageRole,
    TERMINAL_STAGES,
    WorkspaceMessage,
    WorkspaceRun,
    WorkspaceRunNotFound,
    WorkspaceStage,
    WorkspaceTransitionError,
)


_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS workspace_messages (
    message_id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    task_id TEXT
);
CREATE INDEX IF NOT EXISTS workspace_messages_recent
    ON workspace_messages(created_at DESC);
CREATE INDEX IF NOT EXISTS workspace_messages_expiry
    ON workspace_messages(expires_at);
CREATE TABLE IF NOT EXISTS workspace_runs (
    run_id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    stage_started_at TEXT NOT NULL,
    finished_at TEXT,
    user_message_id TEXT REFERENCES workspace_messages(message_id) ON DELETE SET NULL,
    assistant_message_id TEXT REFERENCES workspace_messages(message_id) ON DELETE SET NULL,
    task_id TEXT,
    approval_request_id TEXT
);
CREATE INDEX IF NOT EXISTS workspace_runs_recent
    ON workspace_runs(updated_at DESC);
CREATE TABLE IF NOT EXISTS workspace_run_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES workspace_runs(run_id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS workspace_events_by_run
    ON workspace_run_events(run_id, sequence);
PRAGMA user_version = 2;
"""

_TRANSITIONS = {
    WorkspaceStage.RECEIVED: {WorkspaceStage.UNDERSTANDING, WorkspaceStage.FAILED},
    WorkspaceStage.UNDERSTANDING: {
        WorkspaceStage.PLANNING,
        WorkspaceStage.SUMMARIZING,
        WorkspaceStage.COMPLETED,
        WorkspaceStage.FAILED,
    },
    WorkspaceStage.PLANNING: {
        WorkspaceStage.EXECUTING,
        WorkspaceStage.AWAITING_APPROVAL,
        WorkspaceStage.SUMMARIZING,
        WorkspaceStage.FAILED,
    },
    WorkspaceStage.EXECUTING: {
        WorkspaceStage.AWAITING_APPROVAL,
        WorkspaceStage.SUMMARIZING,
        WorkspaceStage.FAILED,
        WorkspaceStage.CANCELLED,
    },
    WorkspaceStage.AWAITING_APPROVAL: {
        WorkspaceStage.EXECUTING,
        WorkspaceStage.CANCELLED,
        WorkspaceStage.FAILED,
    },
    WorkspaceStage.SUMMARIZING: {WorkspaceStage.COMPLETED, WorkspaceStage.FAILED},
}

_STAGE_LABELS = {
    WorkspaceStage.RECEIVED: "Запрос принят",
    WorkspaceStage.UNDERSTANDING: "Понимаю задачу",
    WorkspaceStage.PLANNING: "Готовлю безопасный план",
    WorkspaceStage.EXECUTING: "Выполняю",
    WorkspaceStage.AWAITING_APPROVAL: "Ожидаю подтверждение",
    WorkspaceStage.SUMMARIZING: "Готовлю результат",
    WorkspaceStage.COMPLETED: "Готово",
    WorkspaceStage.FAILED: "Не выполнено",
    WorkspaceStage.CANCELLED: "Отменено",
}


class WorkspaceStore:
    def __init__(
        self,
        database: Path,
        *,
        retention_hours: int = 24,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        if not 1 <= retention_hours <= 168:
            raise ValueError("retention_hours must be between 1 and 168")
        self.database = Path(database)
        self.retention_hours = retention_hours
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._lock = RLock()
        if self.database.exists() and self.database.is_symlink():
            raise ValueError("workspace database cannot be a symlink")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(_SCHEMA)
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(workspace_runs)")
            }
            if "approval_request_id" not in columns:
                connection.execute(
                    "ALTER TABLE workspace_runs ADD COLUMN approval_request_id TEXT"
                )
        if os.name == "posix":
            os.chmod(self.database, 0o600)

    def append_message(
        self,
        role: MessageRole,
        kind: MessageKind,
        content: str,
        *,
        task_id: str | None = None,
        message_id: str | None = None,
    ) -> WorkspaceMessage:
        if not isinstance(role, MessageRole) or not isinstance(kind, MessageKind):
            raise ValueError("role and kind must use workspace enums")
        text = self._content(content)
        identifier = message_id or str(uuid4())
        self._uuid(identifier, "message_id")
        if task_id is not None:
            self._uuid(task_id, "task_id")
        now = self._now()
        expires = now + timedelta(hours=self.retention_hours)
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO workspace_messages(
                    message_id, role, kind, content, created_at, expires_at, task_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    identifier,
                    role.value,
                    kind.value,
                    text,
                    self._timestamp(now),
                    self._timestamp(expires),
                    task_id,
                ),
            )
        return self.message(identifier)

    def create_run(
        self, user_message_id: str, *, run_id: str | None = None
    ) -> WorkspaceRun:
        identifier = run_id or str(uuid4())
        self._uuid(identifier, "run_id")
        self._uuid(user_message_id, "user_message_id")
        now = self._timestamp(self._now())
        with self._transaction() as connection:
            message = self._message_row(connection, user_message_id)
            if MessageRole(message["role"]) is not MessageRole.USER:
                raise ValueError("workspace run must start from a user message")
            connection.execute(
                """INSERT INTO workspace_runs(
                    run_id, stage, created_at, updated_at, started_at,
                    stage_started_at, user_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    identifier,
                    WorkspaceStage.RECEIVED.value,
                    now,
                    now,
                    now,
                    now,
                    user_message_id,
                ),
            )
            self._event(connection, identifier, WorkspaceStage.RECEIVED, now)
        return self.run(identifier)

    def transition(self, run_id: str, stage: WorkspaceStage) -> WorkspaceRun:
        if not isinstance(stage, WorkspaceStage):
            raise ValueError("stage must use WorkspaceStage")
        now = self._timestamp(self._now())
        with self._transaction() as connection:
            row = self._run_row(connection, run_id)
            current = WorkspaceStage(row["stage"])
            if stage not in _TRANSITIONS.get(current, set()):
                raise WorkspaceTransitionError("workspace_stage_transition_invalid")
            finished = now if stage in TERMINAL_STAGES else None
            connection.execute(
                """UPDATE workspace_runs SET stage = ?, updated_at = ?,
                   stage_started_at = ?, finished_at = COALESCE(?, finished_at)
                   WHERE run_id = ?""",
                (stage.value, now, now, finished, run_id),
            )
            self._event(connection, run_id, stage, now)
        return self.run(run_id)

    def complete(
        self,
        run_id: str,
        assistant_message_id: str,
        *,
        task_id: str | None = None,
    ) -> WorkspaceRun:
        return self.finish(
            run_id,
            WorkspaceStage.COMPLETED,
            assistant_message_id,
            task_id=task_id,
        )

    def finish(
        self,
        run_id: str,
        stage: WorkspaceStage,
        assistant_message_id: str,
        *,
        task_id: str | None = None,
    ) -> WorkspaceRun:
        if stage not in TERMINAL_STAGES:
            raise ValueError("finish stage must be terminal")
        self._uuid(assistant_message_id, "assistant_message_id")
        if task_id is not None:
            self._uuid(task_id, "task_id")
        with self._transaction() as connection:
            message = self._message_row(connection, assistant_message_id)
            if MessageRole(message["role"]) is not MessageRole.ASSISTANT:
                raise ValueError("completion requires an assistant message")
            if message["task_id"] != task_id:
                raise ValueError("assistant message and workspace run task_id must match")
            row = self._run_row(connection, run_id)
            current = WorkspaceStage(row["stage"])
            if stage not in _TRANSITIONS.get(current, set()):
                raise WorkspaceTransitionError("workspace_completion_invalid")
            now = self._timestamp(self._now())
            connection.execute(
                """UPDATE workspace_runs SET stage = ?, updated_at = ?,
                   stage_started_at = ?, finished_at = ?, assistant_message_id = ?, task_id = ?
                   WHERE run_id = ?""",
                (
                    stage.value,
                    now,
                    now,
                    now,
                    assistant_message_id,
                    task_id,
                    run_id,
                ),
            )
            self._event(connection, run_id, stage, now)
        return self.run(run_id)

    def link_task(self, run_id: str, task_id: str) -> WorkspaceRun:
        self._uuid(task_id, "task_id")
        now = self._timestamp(self._now())
        with self._transaction() as connection:
            row = self._run_row(connection, run_id)
            if WorkspaceStage(row["stage"]) in TERMINAL_STAGES:
                raise WorkspaceTransitionError("workspace_task_link_too_late")
            connection.execute(
                "UPDATE workspace_runs SET task_id = ?, updated_at = ? WHERE run_id = ?",
                (task_id, now, run_id),
            )
        return self.run(run_id)

    def awaiting_approval(
        self, run_id: str, *, task_id: str, approval_request_id: str
    ) -> WorkspaceRun:
        self._uuid(task_id, "task_id")
        self._uuid(approval_request_id, "approval_request_id")
        now = self._timestamp(self._now())
        with self._transaction() as connection:
            row = self._run_row(connection, run_id)
            current = WorkspaceStage(row["stage"])
            if WorkspaceStage.AWAITING_APPROVAL not in _TRANSITIONS.get(current, set()):
                raise WorkspaceTransitionError("workspace_approval_transition_invalid")
            connection.execute(
                """UPDATE workspace_runs SET stage = ?, updated_at = ?,
                   stage_started_at = ?, task_id = ?, approval_request_id = ?
                   WHERE run_id = ?""",
                (
                    WorkspaceStage.AWAITING_APPROVAL.value,
                    now,
                    now,
                    task_id,
                    approval_request_id,
                    run_id,
                ),
            )
            self._event(connection, run_id, WorkspaceStage.AWAITING_APPROVAL, now)
        return self.run(run_id)

    def run_for_approval(self, approval_request_id: str) -> WorkspaceRun:
        self._uuid(approval_request_id, "approval_request_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workspace_runs WHERE approval_request_id = ?",
                (approval_request_id,),
            ).fetchone()
            if row is None:
                raise WorkspaceRunNotFound("workspace_approval_not_found")
            return self._run(row)

    def message(self, message_id: str) -> WorkspaceMessage:
        with self._connect() as connection:
            return self._message(self._message_row(connection, message_id))

    def list_messages(self, *, limit: int = 200) -> tuple[WorkspaceMessage, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer from 1 to 500")
        now = self._timestamp(self._now())
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM workspace_messages WHERE expires_at > ?
                   ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                (now, limit),
            ).fetchall()
        return tuple(self._message(row) for row in reversed(rows))

    def run(self, run_id: str) -> WorkspaceRun:
        with self._connect() as connection:
            return self._run(self._run_row(connection, run_id))

    def list_runs(
        self, *, limit: int = 20, active_only: bool = False
    ) -> tuple[WorkspaceRun, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100")
        if not isinstance(active_only, bool):
            raise ValueError("active_only must be a boolean")
        where = "WHERE finished_at IS NULL" if active_only else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM workspace_runs {where}
                    ORDER BY updated_at DESC, rowid DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return tuple(self._run(row) for row in rows)

    def purge_expired(self) -> tuple[int, int]:
        current = self._now()
        now = self._timestamp(current)
        run_cutoff = self._timestamp(
            current - timedelta(hours=self.retention_hours)
        )
        with self._transaction() as connection:
            messages = connection.execute(
                "DELETE FROM workspace_messages WHERE expires_at <= ?", (now,)
            ).rowcount
            terminal = tuple(stage.value for stage in TERMINAL_STAGES)
            placeholders = ",".join("?" for _ in terminal)
            runs = connection.execute(
                f"""DELETE FROM workspace_runs WHERE finished_at IS NOT NULL
                    AND finished_at <= ? AND stage IN ({placeholders})""",
                (run_cutoff, *terminal),
            ).rowcount
        return messages, runs

    def recover_after_restart(self, *, message: str) -> tuple[str, ...]:
        """Close orphaned jobs so the panel never shows stale execution forever."""
        content = self._content(message)
        recovered: list[str] = []
        while True:
            active = self.list_runs(limit=100, active_only=True)
            if not active:
                break
            recovered_before = len(recovered)
            for run in active:
                notice = self.append_message(
                    MessageRole.ASSISTANT,
                    MessageKind.NOTICE,
                    content,
                    task_id=run.task_id,
                )
                try:
                    self.finish(
                        run.run_id,
                        WorkspaceStage.FAILED,
                        notice.message_id,
                        task_id=run.task_id,
                    )
                except WorkspaceTransitionError:
                    continue
                recovered.append(run.run_id)
            if len(recovered) == recovered_before:
                break
        return tuple(recovered)

    def _run(self, row: sqlite3.Row) -> WorkspaceRun:
        now = self._now()
        started = self._parse(row["started_at"])
        stage_started = self._parse(row["stage_started_at"])
        finished = self._parse(row["finished_at"]) if row["finished_at"] else None
        endpoint = finished or now
        stage_endpoint = finished or now
        stage = WorkspaceStage(row["stage"])
        return WorkspaceRun(
            run_id=row["run_id"],
            stage=stage,
            stage_label=_STAGE_LABELS[stage],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            elapsed_ms=max(0, int((endpoint - started).total_seconds() * 1000)),
            stage_elapsed_ms=max(0, int((stage_endpoint - stage_started).total_seconds() * 1000)),
            user_message_id=row["user_message_id"],
            assistant_message_id=row["assistant_message_id"],
            task_id=row["task_id"],
            approval_request_id=row["approval_request_id"],
        )

    @staticmethod
    def _message(row: sqlite3.Row) -> WorkspaceMessage:
        return WorkspaceMessage(
            row["message_id"],
            MessageRole(row["role"]),
            MessageKind(row["kind"]),
            row["content"],
            row["created_at"],
            row["expires_at"],
            row["task_id"],
        )

    def _message_row(self, connection: sqlite3.Connection, message_id: str) -> sqlite3.Row:
        self._uuid(message_id, "message_id")
        row = connection.execute(
            "SELECT * FROM workspace_messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        if row is None:
            raise KeyError("workspace_message_not_found")
        return row

    def _run_row(self, connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        self._uuid(run_id, "run_id")
        row = connection.execute(
            "SELECT * FROM workspace_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise WorkspaceRunNotFound("workspace_run_not_found")
        return row

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        run_id: str,
        stage: WorkspaceStage,
        occurred_at: str,
    ) -> None:
        connection.execute(
            "INSERT INTO workspace_run_events(run_id, stage, occurred_at) VALUES (?, ?, ?)",
            (run_id, stage.value, occurred_at),
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

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _now(self) -> datetime:
        value = self._now_fn()
        if value.tzinfo is None:
            raise ValueError("workspace clock must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.astimezone(UTC).isoformat(timespec="milliseconds")

    @staticmethod
    def _parse(value: str) -> datetime:
        return datetime.fromisoformat(value).astimezone(UTC)

    @staticmethod
    def _uuid(value: str, label: str) -> None:
        try:
            UUID(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{label} must be a UUID") from error

    @staticmethod
    def _content(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("message content must be a string")
        text = value.strip()
        if not text or len(text) > 16_000:
            raise ValueError("message content must contain from 1 to 16000 characters")
        if any(ord(character) < 32 and character not in "\n\t" for character in text):
            raise ValueError("message content contains control characters")
        return text
