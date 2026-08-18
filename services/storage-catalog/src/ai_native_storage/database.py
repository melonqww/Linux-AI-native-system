"""Shared SQLite database for storage components."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


SCHEMA_VERSION = 2


class StorageDatabase:
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
            self._migrate(connection)
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS storage_schema (
                version INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS volumes (
                volume_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                mount_point TEXT NOT NULL,
                device TEXT NOT NULL,
                fs_type TEXT NOT NULL,
                is_system INTEGER NOT NULL,
                is_removable INTEGER NOT NULL,
                is_network INTEGER NOT NULL,
                is_available INTEGER NOT NULL,
                permission TEXT NOT NULL,
                total_bytes INTEGER NOT NULL DEFAULT 0,
                free_bytes INTEGER NOT NULL DEFAULT 0,
                detected_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS catalog_entries (
                id INTEGER PRIMARY KEY,
                volume_id TEXT NOT NULL REFERENCES volumes(volume_id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                stable_key TEXT NOT NULL,
                search_name TEXT NOT NULL,
                name TEXT NOT NULL,
                extension TEXT NOT NULL,
                mime_type TEXT,
                entry_type TEXT NOT NULL,
                role TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                sensitive INTEGER NOT NULL,
                last_seen_scan TEXT NOT NULL,
                UNIQUE(volume_id, path)
            );
            CREATE INDEX IF NOT EXISTS catalog_by_volume ON catalog_entries(volume_id);
            CREATE INDEX IF NOT EXISTS catalog_by_extension ON catalog_entries(extension);
            CREATE INDEX IF NOT EXISTS catalog_by_role ON catalog_entries(role);
            CREATE INDEX IF NOT EXISTS catalog_by_stable_key ON catalog_entries(volume_id, stable_key);

            CREATE TABLE IF NOT EXISTS collections (
                collection_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                kind TEXT NOT NULL,
                query_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS collection_items (
                collection_id TEXT NOT NULL REFERENCES collections(collection_id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                volume_id TEXT NOT NULL,
                path TEXT NOT NULL,
                stable_key TEXT NOT NULL,
                name TEXT NOT NULL,
                score REAL,
                snippet TEXT,
                PRIMARY KEY(collection_id, ordinal)
            );
            """
        )
        row = connection.execute("SELECT version FROM storage_schema LIMIT 1").fetchone()
        if row is None:
            connection.execute("INSERT INTO storage_schema(version) VALUES (?)", (SCHEMA_VERSION,))
        elif int(row["version"]) == 1:
            columns = {
                str(column["name"])
                for column in connection.execute("PRAGMA table_info(volumes)").fetchall()
            }
            if "total_bytes" not in columns:
                connection.execute(
                    "ALTER TABLE volumes ADD COLUMN total_bytes INTEGER NOT NULL DEFAULT 0"
                )
            if "free_bytes" not in columns:
                connection.execute(
                    "ALTER TABLE volumes ADD COLUMN free_bytes INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute("UPDATE storage_schema SET version = ?", (SCHEMA_VERSION,))
        elif int(row["version"]) != SCHEMA_VERSION:
            raise RuntimeError(
                f"unsupported storage schema {row['version']}; expected {SCHEMA_VERSION}"
            )
