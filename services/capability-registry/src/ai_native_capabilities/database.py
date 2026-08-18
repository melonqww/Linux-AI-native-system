"""SQLite persistence for module state and published capabilities."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


SCHEMA_VERSION = 1


class RegistryDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            self._create_schema(connection)
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS registry_schema (
                version INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS modules (
                module_id TEXT PRIMARY KEY,
                manifest_json TEXT NOT NULL,
                manifest_path TEXT NOT NULL UNIQUE,
                manifest_hash TEXT NOT NULL,
                manifest_valid INTEGER NOT NULL,
                module_version TEXT NOT NULL,
                core_api TEXT NOT NULL,
                desired_enabled INTEGER NOT NULL,
                user_configured INTEGER NOT NULL DEFAULT 0,
                quarantined INTEGER NOT NULL DEFAULT 0,
                quarantine_reason TEXT,
                state TEXT NOT NULL,
                state_reason TEXT,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS module_capabilities (
                module_id TEXT NOT NULL REFERENCES modules(module_id) ON DELETE CASCADE,
                capability_id TEXT NOT NULL,
                PRIMARY KEY(module_id, capability_id)
            );
            CREATE INDEX IF NOT EXISTS capabilities_by_id
                ON module_capabilities(capability_id);
            """
        )
        row = connection.execute("SELECT version FROM registry_schema LIMIT 1").fetchone()
        if row is None:
            connection.execute("INSERT INTO registry_schema(version) VALUES (?)", (SCHEMA_VERSION,))
        elif int(row["version"]) != SCHEMA_VERSION:
            raise RuntimeError(
                f"unsupported capability registry schema {row['version']}; expected {SCHEMA_VERSION}"
            )
