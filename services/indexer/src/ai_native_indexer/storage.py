"""SQLite metadata and full-text search storage."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .chunking import TextChunk
from .contracts import ContentIndexState, ContentIndexStatus, SearchHit


INDEX_FORMAT_VERSION = 1


class IndexStorage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        self._create_schema(connection)
        return connection

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current_version != INDEX_FORMAT_VERSION:
            connection.executescript(
                """
                DROP TABLE IF EXISTS chunks_fts;
                DROP TABLE IF EXISTS sources;
                DROP TABLE IF EXISTS source_states;
                """
            )
        connection.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY,
                root_path TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                size_bytes INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                content_hash TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                source_id UNINDEXED,
                path UNINDEXED,
                ordinal UNINDEXED,
                line_start UNINDEXED,
                line_end UNINDEXED,
                content,
                tokenize = 'unicode61'
            );
            CREATE TABLE IF NOT EXISTS source_states (
                path TEXT PRIMARY KEY,
                size_bytes INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                state TEXT NOT NULL,
                reason TEXT
            );
            PRAGMA user_version = {INDEX_FORMAT_VERSION};
            """
        )
        connection.execute(
            """INSERT OR IGNORE INTO source_states(
                   path, size_bytes, mtime_ns, state, reason
               )
               SELECT path, size_bytes, mtime_ns, ?, NULL FROM sources""",
            (ContentIndexState.INDEXED.value,),
        )

    @staticmethod
    def source_metadata(connection: sqlite3.Connection, path: str) -> tuple[int, int] | None:
        row = connection.execute("SELECT size_bytes, mtime_ns FROM sources WHERE path = ?", (path,)).fetchone()
        if row is None:
            return None
        return int(row["size_bytes"]), int(row["mtime_ns"])

    @staticmethod
    def replace_source(
        connection: sqlite3.Connection,
        *,
        root_path: str,
        path: str,
        size_bytes: int,
        mtime_ns: int,
        content_hash: str,
        chunks: list[TextChunk],
    ) -> bool:
        existing = connection.execute("SELECT id FROM sources WHERE path = ?", (path,)).fetchone()
        was_update = existing is not None
        if existing is not None:
            source_id = int(existing["id"])
            connection.execute("DELETE FROM chunks_fts WHERE source_id = ?", (source_id,))
            connection.execute(
                "UPDATE sources SET root_path = ?, size_bytes = ?, mtime_ns = ?, content_hash = ? WHERE id = ?",
                (root_path, size_bytes, mtime_ns, content_hash, source_id),
            )
        else:
            cursor = connection.execute(
                "INSERT INTO sources(root_path, path, size_bytes, mtime_ns, content_hash) VALUES (?, ?, ?, ?, ?)",
                (root_path, path, size_bytes, mtime_ns, content_hash),
            )
            source_id = int(cursor.lastrowid)

        connection.executemany(
            """
            INSERT INTO chunks_fts(source_id, path, ordinal, line_start, line_end, content)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (source_id, path, chunk.ordinal, chunk.line_start, chunk.line_end, chunk.content)
                for chunk in chunks
            ],
        )
        connection.execute(
            """INSERT INTO source_states(path, size_bytes, mtime_ns, state, reason)
               VALUES (?, ?, ?, ?, NULL)
               ON CONFLICT(path) DO UPDATE SET
                 size_bytes = excluded.size_bytes,
                 mtime_ns = excluded.mtime_ns,
                 state = excluded.state,
                 reason = NULL""",
            (path, size_bytes, mtime_ns, ContentIndexState.INDEXED.value),
        )
        return was_update

    @classmethod
    def record_state(
        cls,
        connection: sqlite3.Connection,
        *,
        path: str,
        size_bytes: int,
        mtime_ns: int,
        state: ContentIndexState,
        reason: str | None,
    ) -> None:
        if state is ContentIndexState.INDEXED:
            raise ValueError("indexed state must be written with indexed content")
        cls.remove_source(connection, path, remove_state=False)
        connection.execute(
            """INSERT INTO source_states(path, size_bytes, mtime_ns, state, reason)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                 size_bytes = excluded.size_bytes,
                 mtime_ns = excluded.mtime_ns,
                 state = excluded.state,
                 reason = excluded.reason""",
            (path, size_bytes, mtime_ns, state.value, reason),
        )

    @staticmethod
    def states_by_paths(
        connection: sqlite3.Connection, paths: list[str]
    ) -> dict[str, ContentIndexStatus]:
        result: dict[str, ContentIndexStatus] = {}
        for start in range(0, len(paths), 500):
            batch = paths[start : start + 500]
            if not batch:
                continue
            rows = connection.execute(
                f"SELECT path, state, reason FROM source_states "
                f"WHERE path IN ({','.join('?' for _ in batch)})",
                batch,
            ).fetchall()
            result.update(
                (
                    str(row["path"]),
                    ContentIndexStatus(
                        str(row["path"]),
                        ContentIndexState(str(row["state"])),
                        None if row["reason"] is None else str(row["reason"]),
                    ),
                )
                for row in rows
            )
        return result

    @staticmethod
    def remove_missing(connection: sqlite3.Connection, root_path: str, present_paths: set[str]) -> int:
        rows = connection.execute("SELECT id, path FROM sources WHERE root_path = ?", (root_path,)).fetchall()
        missing = [row for row in rows if str(row["path"]) not in present_paths]
        for row in missing:
            connection.execute("DELETE FROM chunks_fts WHERE source_id = ?", (int(row["id"]),))
            connection.execute("DELETE FROM sources WHERE id = ?", (int(row["id"]),))
            connection.execute("DELETE FROM source_states WHERE path = ?", (str(row["path"]),))
        return len(missing)

    @staticmethod
    def remove_source(
        connection: sqlite3.Connection, path: str, *, remove_state: bool = True
    ) -> bool:
        row = connection.execute("SELECT id FROM sources WHERE path = ?", (path,)).fetchone()
        removed = row is not None
        if row is not None:
            source_id = int(row["id"])
            connection.execute("DELETE FROM chunks_fts WHERE source_id = ?", (source_id,))
            connection.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        if remove_state:
            state_cursor = connection.execute("DELETE FROM source_states WHERE path = ?", (path,))
            removed = removed or state_cursor.rowcount == 1
        return removed

    @staticmethod
    def search(connection: sqlite3.Connection, query: str, limit: int) -> list[SearchHit]:
        terms = re.findall(r"[^\W_]+", query, flags=re.UNICODE)
        if not terms:
            return []
        match_query = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
        rows = connection.execute(
            """
            WITH matching AS (
                SELECT path, ordinal, line_start, line_end, content,
                       bm25(chunks_fts) AS rank
                FROM chunks_fts
                WHERE chunks_fts MATCH ?
            ), ranked AS (
                SELECT *, row_number() OVER (
                    PARTITION BY path ORDER BY rank, ordinal
                ) AS position
                FROM matching
            )
            SELECT path, line_start, line_end, content, rank
            FROM ranked
            WHERE position = 1
            ORDER BY rank, path
            LIMIT ?
            """,
            (match_query, limit),
        ).fetchall()
        return [
            SearchHit(
                path=str(row["path"]),
                line_start=int(row["line_start"]),
                line_end=int(row["line_end"]),
                content=str(row["content"]),
                score=float(-row["rank"]),
            )
            for row in rows
        ]

    @staticmethod
    def chunks_by_paths(
        connection: sqlite3.Connection, paths: list[str], limit: int
    ) -> list[SearchHit]:
        """Return a bounded semantic corpus for paths authorized by the caller.

        This deliberately requires an explicit path allow-list.  The index has no
        knowledge of volume permissions, so it must never expose its whole corpus
        to an embedding provider and filter the result afterwards.
        """
        if not paths:
            return []
        unique_paths = list(dict.fromkeys(paths))
        rows: list[sqlite3.Row] = []
        remaining = limit
        for start in range(0, len(unique_paths), 400):
            if remaining <= 0:
                break
            batch = unique_paths[start : start + 400]
            selected = connection.execute(
                f"""
                SELECT path, ordinal, line_start, line_end, content
                FROM chunks_fts
                WHERE path IN ({','.join('?' for _ in batch)})
                  AND CAST(ordinal AS INTEGER) < 2
                ORDER BY path, CAST(ordinal AS INTEGER)
                LIMIT ?
                """,
                (*batch, remaining),
            ).fetchall()
            rows.extend(selected)
            remaining -= len(selected)
        return [
            SearchHit(
                path=str(row["path"]),
                line_start=int(row["line_start"]),
                line_end=int(row["line_end"]),
                content=str(row["content"]),
                score=0.0,
            )
            for row in rows
        ]

    @staticmethod
    def status(connection: sqlite3.Connection) -> dict[str, int]:
        sources = int(connection.execute("SELECT count(*) FROM sources").fetchone()[0])
        chunks = int(connection.execute("SELECT count(*) FROM chunks_fts").fetchone()[0])
        unavailable = int(
            connection.execute(
                "SELECT count(*) FROM source_states WHERE state = ?",
                (ContentIndexState.UNAVAILABLE.value,),
            ).fetchone()[0]
        )
        unsupported = int(
            connection.execute(
                "SELECT count(*) FROM source_states WHERE state = ?",
                (ContentIndexState.UNSUPPORTED.value,),
            ).fetchone()[0]
        )
        return {
            "sources": sources,
            "chunks": chunks,
            "unavailable": unavailable,
            "unsupported": unsupported,
        }
