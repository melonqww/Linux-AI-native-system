"""Durable, content-free crawl frontier stored beside the volume registry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ai_native_storage.database import StorageDatabase


@dataclass(frozen=True)
class CrawlCheckpoint:
    volume_id: str
    root: Path
    root_device: int
    scan_id: str
    current_directory: Path | None
    current_offset: int
    protected_prefixes: tuple[str, ...]


class CrawlCheckpointStore:
    def __init__(self, database: Path) -> None:
        self.database = StorageDatabase(database)
        with self.database.connect():
            pass

    def begin(self, checkpoint: CrawlCheckpoint) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM index_crawl_checkpoints WHERE volume_id = ?",
                (checkpoint.volume_id,),
            )
            connection.execute(
                """INSERT INTO index_crawl_checkpoints(
                    volume_id, root, root_device, scan_id, current_directory,
                    current_offset, updated_at
                ) VALUES (?, ?, ?, ?, NULL, 0, ?)""",
                (
                    checkpoint.volume_id,
                    str(checkpoint.root),
                    checkpoint.root_device,
                    checkpoint.scan_id,
                    self._now(),
                ),
            )
            connection.execute(
                "INSERT INTO index_crawl_directories(volume_id, path) VALUES (?, ?)",
                (checkpoint.volume_id, str(checkpoint.root)),
            )

    def load_all(self) -> tuple[CrawlCheckpoint, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM index_crawl_checkpoints ORDER BY updated_at"
            ).fetchall()
            result = []
            for row in rows:
                protected = connection.execute(
                    "SELECT path FROM index_crawl_protected WHERE volume_id = ? ORDER BY path",
                    (row["volume_id"],),
                ).fetchall()
                result.append(
                    CrawlCheckpoint(
                        str(row["volume_id"]),
                        Path(str(row["root"])),
                        int(row["root_device"]),
                        str(row["scan_id"]),
                        Path(str(row["current_directory"]))
                        if row["current_directory"] is not None
                        else None,
                        int(row["current_offset"]),
                        tuple(str(value["path"]) for value in protected),
                    )
                )
            return tuple(result)

    def claim_directory(self, volume_id: str) -> tuple[Path, int] | None:
        with self._connect() as connection:
            checkpoint = connection.execute(
                "SELECT current_directory, current_offset FROM index_crawl_checkpoints WHERE volume_id = ?",
                (volume_id,),
            ).fetchone()
            if checkpoint is None:
                return None
            if checkpoint["current_directory"] is not None:
                return Path(str(checkpoint["current_directory"])), int(
                    checkpoint["current_offset"]
                )
            queued = connection.execute(
                "SELECT id, path FROM index_crawl_directories WHERE volume_id = ? ORDER BY id LIMIT 1",
                (volume_id,),
            ).fetchone()
            if queued is None:
                return None
            connection.execute(
                "DELETE FROM index_crawl_directories WHERE id = ?", (queued["id"],)
            )
            connection.execute(
                """UPDATE index_crawl_checkpoints
                   SET current_directory = ?, current_offset = 0, updated_at = ?
                   WHERE volume_id = ?""",
                (queued["path"], self._now(), volume_id),
            )
            return Path(str(queued["path"])), 0

    def enqueue(self, volume_id: str, path: Path) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO index_crawl_directories(volume_id, path) VALUES (?, ?)",
                (volume_id, str(path)),
            )

    def save_offset(self, volume_id: str, offset: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE index_crawl_checkpoints SET current_offset = ?, updated_at = ? WHERE volume_id = ?",
                (offset, self._now(), volume_id),
            )

    def complete_directory(self, volume_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE index_crawl_checkpoints SET current_directory = NULL,
                   current_offset = 0, updated_at = ? WHERE volume_id = ?""",
                (self._now(), volume_id),
            )

    def protect(self, volume_id: str, path: Path) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO index_crawl_protected(volume_id, path) VALUES (?, ?)",
                (volume_id, str(path.absolute())),
            )

    def has_pending(self, volume_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT EXISTS(
                    SELECT 1 FROM index_crawl_directories WHERE volume_id = ?
                    UNION ALL
                    SELECT 1 FROM index_crawl_checkpoints
                    WHERE volume_id = ? AND current_directory IS NOT NULL
                ) AS pending""",
                (volume_id, volume_id),
            ).fetchone()
            return bool(row["pending"])

    def discard(self, volume_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM index_crawl_checkpoints WHERE volume_id = ?", (volume_id,)
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database.path, timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            with connection:
                yield connection
        finally:
            connection.close()
