from __future__ import annotations

import os
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path


DECISIONS = frozenset({"install", "later", "never"})


class ProviderDecisionStore:
    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self._lock = threading.RLock()
        if self.database.exists() and self.database.is_symlink():
            raise ValueError("provider state database cannot be a symlink")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS provider_decision (
                    provider_id TEXT PRIMARY KEY,
                    decision TEXT NOT NULL,
                    dismissed_until TEXT,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute("PRAGMA user_version = 1")
        if os.name == "posix":
            os.chmod(self.database, 0o600)

    def get(self) -> tuple[str | None, datetime | None]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT decision, dismissed_until FROM provider_decision WHERE provider_id = ?",
                ("ollama",),
            ).fetchone()
        if row is None:
            return None, None
        dismissed = datetime.fromisoformat(row[1]).astimezone(UTC) if row[1] else None
        return str(row[0]), dismissed

    def set(self, decision: str, *, now: datetime) -> None:
        if decision not in DECISIONS:
            raise ValueError("unsupported provider decision")
        dismissed = now + timedelta(hours=24) if decision == "later" else None
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO provider_decision(provider_id, decision, dismissed_until, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(provider_id) DO UPDATE SET decision = excluded.decision,
                   dismissed_until = excluded.dismissed_until,
                   updated_at = excluded.updated_at""",
                (
                    "ollama",
                    decision,
                    dismissed.isoformat() if dismissed else None,
                    now.isoformat(),
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
