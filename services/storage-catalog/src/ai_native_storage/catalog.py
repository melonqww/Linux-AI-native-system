"""Incremental metadata catalog bounded to one registered volume."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from .classifier import is_sensitive, mime_type_for, project_role_for_names, role_for
from .contracts import (
    CatalogEntry,
    CatalogScanReport,
    EntryType,
    FileQuery,
    PermissionLevel,
)
from .database import StorageDatabase
from .registry import VolumeRegistry


@dataclass
class _ScanState:
    discovered: int = 0
    inaccessible: int = 0
    skipped_mounts: int = 0


class FileCatalog:
    _BATCH_SIZE = 500

    def __init__(self, database_path: Path) -> None:
        self.database = StorageDatabase(database_path)
        self.registry = VolumeRegistry(database_path)

    def scan_volume(self, volume_id: str) -> CatalogScanReport:
        volume = self.registry.get_volume(volume_id)
        if not volume.is_available:
            raise RuntimeError(f"volume is not available: {volume_id}")
        if volume.permission is PermissionLevel.NONE:
            raise PermissionError(f"metadata access is not allowed for volume: {volume_id}")

        root = Path(volume.mount_point).resolve(strict=True)
        root_device = root.stat().st_dev
        scan_id = str(uuid4())
        started = perf_counter()
        state = _ScanState()
        protected_prefixes: list[str] = []
        batch: list[CatalogEntry] = []

        def on_error(error: OSError) -> None:
            state.inaccessible += 1
            if error.filename:
                protected_prefixes.append(str(Path(error.filename).resolve(strict=False)))

        with self.database.connect() as connection:
            for current, directory_names, file_names in os.walk(
                root, topdown=True, followlinks=False, onerror=on_error
            ):
                current_path = Path(current)
                kept_directories: list[str] = []
                project_role = project_role_for_names(set(directory_names) | set(file_names))
                if project_role is not None and current_path != root:
                    self._flush(connection, batch, scan_id)
                    connection.execute(
                        """
                        UPDATE catalog_entries SET role = ?, last_seen_scan = ?
                        WHERE volume_id = ? AND path = ?
                        """,
                        (project_role, scan_id, volume_id, os.path.abspath(current_path)),
                    )

                for name in directory_names:
                    child = current_path / name
                    try:
                        stat = child.lstat()
                        if child.is_symlink():
                            entry_type = EntryType.SYMLINK
                        elif stat.st_dev != root_device:
                            state.skipped_mounts += 1
                            continue
                        else:
                            entry_type = EntryType.DIRECTORY
                            kept_directories.append(name)
                        batch.append(self._entry_from_stat(volume_id, child, stat, entry_type))
                        state.discovered += 1
                    except OSError:
                        state.inaccessible += 1
                        protected_prefixes.append(str(child.resolve(strict=False)))
                directory_names[:] = kept_directories
                self._flush_if_needed(connection, batch, scan_id)

                for name in file_names:
                    child = current_path / name
                    try:
                        stat = child.lstat()
                        if stat.st_dev != root_device:
                            state.skipped_mounts += 1
                            continue
                        entry_type = EntryType.SYMLINK if child.is_symlink() else EntryType.FILE
                        batch.append(self._entry_from_stat(volume_id, child, stat, entry_type))
                        state.discovered += 1
                    except OSError:
                        state.inaccessible += 1
                    self._flush_if_needed(connection, batch, scan_id)

            self._flush(connection, batch, scan_id)
            removed = self._remove_unseen(
                connection,
                volume_id=volume_id,
                scan_id=scan_id,
                protected_prefixes=protected_prefixes,
            )

        return CatalogScanReport(
            volume_id=volume_id,
            mount_point=str(root),
            discovered=state.discovered,
            removed=removed,
            inaccessible=state.inaccessible,
            skipped_mounts=state.skipped_mounts,
            duration_ms=round((perf_counter() - started) * 1_000, 3),
        )

    def update_path(
        self,
        volume_id: str,
        path: Path,
        *,
        scan_id: str | None = None,
    ) -> CatalogEntry | None:
        """Upsert or remove one path while enforcing its registered volume boundary."""
        volume = self.registry.get_volume(volume_id)
        if not volume.is_available:
            raise RuntimeError(f"volume is not available: {volume_id}")
        if volume.permission is PermissionLevel.NONE:
            raise PermissionError(f"metadata access is not allowed for volume: {volume_id}")
        root = Path(volume.mount_point).resolve(strict=True)
        candidate = path.expanduser().absolute()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise ValueError("event path is outside the registered volume") from error

        if not candidate.exists() and not candidate.is_symlink():
            self.remove_path(volume_id, candidate)
            return None
        resolved_parent = candidate.parent.resolve(strict=True)
        try:
            resolved_parent.relative_to(root)
        except ValueError as error:
            raise ValueError("event path escapes through a symbolic-link parent") from error
        stat = candidate.lstat()
        if stat.st_dev != root.stat().st_dev:
            raise ValueError("event path crosses a filesystem boundary")
        if candidate.is_symlink():
            entry_type = EntryType.SYMLINK
        elif candidate.is_dir():
            entry_type = EntryType.DIRECTORY
        elif candidate.is_file():
            entry_type = EntryType.FILE
        else:
            self.remove_path(volume_id, candidate)
            return None
        if entry_type is not EntryType.SYMLINK:
            resolved_candidate = candidate.resolve(strict=True)
            try:
                resolved_candidate.relative_to(root)
            except ValueError as error:
                raise ValueError("event path resolves outside the registered volume") from error
        entry = self._entry_from_stat(volume_id, candidate, stat, entry_type)
        with self.database.connect() as connection:
            batch = [entry]
            self._flush(connection, batch, scan_id or f"event:{uuid4()}")
        return entry

    def finish_incremental_scan(
        self,
        volume_id: str,
        scan_id: str,
        *,
        protected_prefixes: list[str] | None = None,
    ) -> tuple[str, ...]:
        with self.database.connect() as connection:
            prefixes = protected_prefixes or []
            rows = connection.execute(
                "SELECT id, path FROM catalog_entries WHERE volume_id = ? AND last_seen_scan != ?",
                (volume_id, scan_id),
            ).fetchall()
            removable = [
                row
                for row in rows
                if not any(
                    str(row["path"]) == prefix
                    or str(row["path"]).startswith(prefix + os.sep)
                    for prefix in prefixes
                )
            ]
            connection.executemany(
                "DELETE FROM catalog_entries WHERE id = ?",
                [(int(row["id"]),) for row in removable],
            )
        return tuple(str(row["path"]) for row in removable)

    def remove_path(self, volume_id: str, path: Path) -> bool:
        volume = self.registry.get_volume(volume_id)
        root = Path(volume.mount_point).resolve(strict=True)
        candidate = path.expanduser().absolute()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise ValueError("event path is outside the registered volume") from error
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM catalog_entries WHERE volume_id = ? AND path = ?",
                (volume_id, str(candidate)),
            )
        return cursor.rowcount == 1

    @staticmethod
    def _entry_from_stat(
        volume_id: str,
        path: Path,
        stat: os.stat_result,
        entry_type: EntryType,
    ) -> CatalogEntry:
        mime_type = mime_type_for(path, entry_type)
        stable_key = f"{stat.st_dev}:{stat.st_ino}" if stat.st_ino else str(path)
        return CatalogEntry(
            volume_id=volume_id,
            path=os.path.abspath(path),
            stable_key=stable_key,
            name=path.name,
            extension=path.suffix.casefold(),
            mime_type=mime_type,
            entry_type=entry_type,
            role=role_for(path, entry_type, mime_type),
            size_bytes=stat.st_size if entry_type is not EntryType.DIRECTORY else 0,
            mtime_ns=stat.st_mtime_ns,
            sensitive=is_sensitive(path),
        )

    def _flush_if_needed(
        self,
        connection: sqlite3.Connection,
        batch: list[CatalogEntry],
        scan_id: str,
    ) -> None:
        if len(batch) >= self._BATCH_SIZE:
            self._flush(connection, batch, scan_id)

    @staticmethod
    def _flush(
        connection: sqlite3.Connection,
        batch: list[CatalogEntry],
        scan_id: str,
    ) -> None:
        if not batch:
            return
        connection.executemany(
            """
            INSERT INTO catalog_entries(
                volume_id, path, stable_key, search_name, name, extension,
                mime_type, entry_type, role, size_bytes, mtime_ns, sensitive,
                last_seen_scan
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(volume_id, path) DO UPDATE SET
                stable_key = excluded.stable_key,
                search_name = excluded.search_name,
                name = excluded.name,
                extension = excluded.extension,
                mime_type = excluded.mime_type,
                entry_type = excluded.entry_type,
                role = excluded.role,
                size_bytes = excluded.size_bytes,
                mtime_ns = excluded.mtime_ns,
                sensitive = excluded.sensitive,
                last_seen_scan = excluded.last_seen_scan
            """,
            [
                (
                    entry.volume_id,
                    entry.path,
                    entry.stable_key,
                    entry.name.casefold(),
                    entry.name,
                    entry.extension,
                    entry.mime_type,
                    entry.entry_type.value,
                    entry.role,
                    entry.size_bytes,
                    entry.mtime_ns,
                    entry.sensitive,
                    scan_id,
                )
                for entry in batch
            ],
        )
        batch.clear()

    @staticmethod
    def _remove_unseen(
        connection: sqlite3.Connection,
        *,
        volume_id: str,
        scan_id: str,
        protected_prefixes: list[str],
    ) -> int:
        rows = connection.execute(
            "SELECT id, path FROM catalog_entries WHERE volume_id = ? AND last_seen_scan != ?",
            (volume_id, scan_id),
        ).fetchall()
        removable_ids: list[int] = []
        for row in rows:
            path = str(row["path"])
            if any(path == prefix or path.startswith(prefix + os.sep) for prefix in protected_prefixes):
                continue
            removable_ids.append(int(row["id"]))
        connection.executemany(
            "DELETE FROM catalog_entries WHERE id = ?",
            [(entry_id,) for entry_id in removable_ids],
        )
        return len(removable_ids)

    def search(
        self,
        query: FileQuery,
        *,
        allow_sensitive_metadata: bool = False,
    ) -> list[CatalogEntry]:
        if not 1 <= query.limit <= 1_000:
            raise ValueError("query limit must be from 1 to 1000")
        if not isinstance(query.offset, int) or isinstance(query.offset, bool) or query.offset < 0:
            raise ValueError("query offset must be a non-negative integer")

        clauses = ["v.is_available = 1", "v.permission != 'none'"]
        parameters: list[object] = []
        for term in query.name_contains:
            escaped = term.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("e.search_name LIKE ? ESCAPE '\\'")
            parameters.append(f"%{escaped}%")
        if query.extensions:
            extensions = [extension.casefold() if extension.startswith(".") else f".{extension.casefold()}" for extension in query.extensions]
            clauses.append(f"e.extension IN ({','.join('?' for _ in extensions)})")
            parameters.extend(extensions)
        if query.roles:
            clauses.append(f"e.role IN ({','.join('?' for _ in query.roles)})")
            parameters.extend(query.roles)
        if query.volume_ids:
            clauses.append(f"e.volume_id IN ({','.join('?' for _ in query.volume_ids)})")
            parameters.extend(query.volume_ids)
        if not allow_sensitive_metadata:
            clauses.append("e.sensitive = 0")

        parameters.extend((query.limit, query.offset))
        sql = f"""
            SELECT e.*
            FROM catalog_entries AS e
            JOIN volumes AS v ON v.volume_id = e.volume_id
            WHERE {' AND '.join(clauses)}
            ORDER BY e.name COLLATE NOCASE, e.path
            LIMIT ? OFFSET ?
        """
        with self.database.connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._from_row(row) for row in rows]

    def count(
        self,
        query: FileQuery,
        *,
        allow_sensitive_metadata: bool = False,
    ) -> int:
        """Count metadata matches without materializing paths or file contents."""
        clauses = ["v.is_available = 1", "v.permission != 'none'"]
        parameters: list[object] = []
        for term in query.name_contains:
            escaped = term.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("e.search_name LIKE ? ESCAPE '\\'")
            parameters.append(f"%{escaped}%")
        if query.extensions:
            extensions = [
                extension.casefold() if extension.startswith(".") else f".{extension.casefold()}"
                for extension in query.extensions
            ]
            clauses.append(f"e.extension IN ({','.join('?' for _ in extensions)})")
            parameters.extend(extensions)
        if query.roles:
            clauses.append(f"e.role IN ({','.join('?' for _ in query.roles)})")
            parameters.extend(query.roles)
        if query.volume_ids:
            clauses.append(f"e.volume_id IN ({','.join('?' for _ in query.volume_ids)})")
            parameters.extend(query.volume_ids)
        if not allow_sensitive_metadata:
            clauses.append("e.sensitive = 0")
        with self.database.connect() as connection:
            row = connection.execute(
                f"""SELECT count(*) FROM catalog_entries AS e
                    JOIN volumes AS v ON v.volume_id = e.volume_id
                    WHERE {' AND '.join(clauses)}""",
                parameters,
            ).fetchone()
        return int(row[0])

    def resolve_reference(
        self,
        *,
        volume_id: str,
        stable_key: str,
        fallback_path: str,
    ) -> CatalogEntry | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT e.* FROM catalog_entries AS e
                JOIN volumes AS v ON v.volume_id = e.volume_id
                WHERE e.volume_id = ? AND (e.stable_key = ? OR e.path = ?)
                  AND v.is_available = 1
                  AND v.permission != 'none'
                ORDER BY e.path = ? DESC, e.stable_key = ? DESC
                LIMIT 1
                """,
                (volume_id, stable_key, fallback_path, fallback_path, stable_key),
            ).fetchone()
        return None if row is None else self._from_row(row)

    def entries_by_paths(self, paths: list[str]) -> dict[str, CatalogEntry]:
        if not paths:
            return {}
        result: dict[str, CatalogEntry] = {}
        with self.database.connect() as connection:
            for start in range(0, len(paths), 500):
                batch = paths[start : start + 500]
                rows = connection.execute(
                    f"""
                    SELECT e.* FROM catalog_entries AS e
                    JOIN volumes AS v ON v.volume_id = e.volume_id
                    WHERE e.path IN ({','.join('?' for _ in batch)})
                      AND e.sensitive = 0
                      AND v.is_available = 1
                      AND v.permission != 'none'
                    """,
                    batch,
                ).fetchall()
                result.update((str(row["path"]), self._from_row(row)) for row in rows)
        return result

    def status(self) -> dict[str, object]:
        with self.database.connect() as connection:
            total = int(connection.execute("SELECT count(*) FROM catalog_entries").fetchone()[0])
            rows = connection.execute(
                "SELECT volume_id, count(*) AS count FROM catalog_entries GROUP BY volume_id"
            ).fetchall()
        return {
            "entries": total,
            "by_volume": {str(row["volume_id"]): int(row["count"]) for row in rows},
        }

    @staticmethod
    def _from_row(row: sqlite3.Row) -> CatalogEntry:
        return CatalogEntry(
            volume_id=str(row["volume_id"]),
            path=str(row["path"]),
            stable_key=str(row["stable_key"]),
            name=str(row["name"]),
            extension=str(row["extension"]),
            mime_type=None if row["mime_type"] is None else str(row["mime_type"]),
            entry_type=EntryType(str(row["entry_type"])),
            role=str(row["role"]),
            size_bytes=int(row["size_bytes"]),
            mtime_ns=int(row["mtime_ns"]),
            sensitive=bool(row["sensitive"]),
        )
