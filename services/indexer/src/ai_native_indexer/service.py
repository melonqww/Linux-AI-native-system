"""Public indexing and retrieval API."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from pathlib import Path

from .chunking import chunk_text
from .contracts import ContentIndexState, ContentIndexStatus, IndexReport, SearchHit
from .file_policy import FilePolicy, read_allowed_text
from .storage import IndexStorage


class IndexerService:
    _REASON = re.compile(r"^[a-z][a-z0-9_]{0,79}$")

    def __init__(self, database_path: Path, *, file_policy: FilePolicy | None = None) -> None:
        self.storage = IndexStorage(database_path)
        self.file_policy = file_policy or FilePolicy()

    def index_directory(self, root: Path) -> IndexReport:
        root = root.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(root)

        root_text = str(root)
        report = IndexReport(root=root_text)
        present_paths: set[str] = set()

        with self.storage.connect() as connection:
            for current, directory_names, file_names in os.walk(root, followlinks=False):
                current_path = Path(current)
                kept_directories: list[str] = []
                for name in directory_names:
                    child = current_path / name
                    reason = self.file_policy.directory_reason(child)
                    if reason:
                        report.add_skipped(reason)
                    else:
                        kept_directories.append(name)
                directory_names[:] = kept_directories

                for name in file_names:
                    path = current_path / name
                    reason = self.file_policy.file_reason(path)
                    if reason:
                        if reason in {
                            "too_large",
                            "unreadable",
                            "unsupported_type",
                            "binary",
                            "invalid_utf8",
                        }:
                            self._record_reason(connection, path.resolve(strict=False), reason)
                        report.add_skipped(reason)
                        continue

                    path = path.resolve(strict=True)
                    try:
                        path.relative_to(root)
                    except ValueError:
                        report.add_skipped("outside_root")
                        continue

                    path_text = str(path)
                    stat = path.stat()
                    metadata = self.storage.source_metadata(connection, path_text)
                    if metadata == (stat.st_size, stat.st_mtime_ns):
                        present_paths.add(path_text)
                        report.unchanged += 1
                        continue

                    text, reason = read_allowed_text(path, self.file_policy)
                    if reason:
                        self._record_reason(connection, path, reason)
                        report.add_skipped(reason)
                        continue
                    assert text is not None
                    present_paths.add(path_text)
                    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    was_update = self.storage.replace_source(
                        connection,
                        root_path=root_text,
                        path=path_text,
                        size_bytes=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                        content_hash=digest,
                        chunks=chunk_text(text),
                    )
                    if was_update:
                        report.updated += 1
                    else:
                        report.indexed += 1

            report.removed = self.storage.remove_missing(connection, root_text, present_paths)
        return report

    def search(self, query: str, *, limit: int = 5) -> list[SearchHit]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            raise ValueError("limit must be an integer from 1 to 50")
        with self.storage.connect() as connection:
            return self.storage.search(connection, query, limit)

    def search_candidates(self, query: str, *, limit: int = 10_001) -> list[SearchHit]:
        """Bounded unique-path candidate set for the cross-module query service."""
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10_001:
            raise ValueError("candidate limit must be an integer from 1 to 10001")
        with self.storage.connect() as connection:
            return self.storage.search(connection, query, limit)

    def semantic_corpus(
        self, paths: list[str], *, limit: int = 2_048
    ) -> list[SearchHit]:
        """Return indexed chunks only for a caller-supplied authorized path set."""
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 2_048:
            raise ValueError("semantic corpus limit must be an integer from 1 to 2048")
        if len(paths) > 1_000 or any(not isinstance(path, str) or not path for path in paths):
            raise ValueError("semantic corpus paths must contain at most 1000 valid paths")
        with self.storage.connect() as connection:
            return self.storage.chunks_by_paths(connection, paths, limit)

    def index_text(self, path: Path, text: str) -> bool:
        """Index trusted text extracted by another capability module."""
        path = path.expanduser()
        if path.is_symlink():
            raise ValueError("source must be a regular non-symlink file")
        path = path.resolve(strict=True)
        if not path.is_file():
            raise ValueError("source must be a regular non-symlink file")
        if not text.strip():
            raise ValueError("extracted text must not be empty")
        stat = path.stat()
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self.storage.connect() as connection:
            if self.storage.source_metadata(connection, str(path)) == (stat.st_size, stat.st_mtime_ns):
                return False
            self.storage.replace_source(
                connection,
                root_path=str(path.parent),
                path=str(path),
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                content_hash=digest,
                chunks=chunk_text(text),
            )
            return True

    def index_file(self, path: Path, *, root: Path | None = None) -> str:
        """Incrementally index one allowed text file and report the outcome."""
        path = path.expanduser()
        reason = self.file_policy.file_reason(path)
        if reason:
            self.record_unavailable(path, reason)
            return f"skipped:{reason}"
        path = path.resolve(strict=True)
        root = path.parent if root is None else root.expanduser().resolve(strict=True)
        try:
            path.relative_to(root)
        except ValueError as error:
            raise ValueError("source is outside the declared root") from error
        text, reason = read_allowed_text(path, self.file_policy)
        if reason:
            self.record_unavailable(path, reason)
            return f"skipped:{reason}"
        assert text is not None
        stat = path.stat()
        with self.storage.connect() as connection:
            if self.storage.source_metadata(connection, str(path)) == (
                stat.st_size,
                stat.st_mtime_ns,
            ):
                return "unchanged"
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            updated = self.storage.replace_source(
                connection,
                root_path=str(root),
                path=str(path),
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                content_hash=digest,
                chunks=chunk_text(text),
            )
        return "updated" if updated else "indexed"

    def remove_path(self, path: Path) -> bool:
        path_text = str(path.expanduser().resolve(strict=False))
        with self.storage.connect() as connection:
            return self.storage.remove_source(connection, path_text)

    def record_unavailable(self, path: Path, reason: str) -> None:
        """Replace stale indexed text with a bounded availability state."""
        if not isinstance(reason, str) or self._REASON.fullmatch(reason) is None:
            raise ValueError("content unavailability reason is invalid")
        candidate = path.expanduser().resolve(strict=False)
        try:
            stat = candidate.stat()
            size_bytes, mtime_ns = stat.st_size, stat.st_mtime_ns
        except OSError:
            size_bytes, mtime_ns = 0, 0
        state = (
            ContentIndexState.UNSUPPORTED
            if reason in {"unsupported_type", "binary", "invalid_utf8"}
            else ContentIndexState.UNAVAILABLE
        )
        with self.storage.connect() as connection:
            self.storage.record_state(
                connection,
                path=str(candidate),
                size_bytes=size_bytes,
                mtime_ns=mtime_ns,
                state=state,
                reason=reason,
            )

    def content_statuses(self, paths: list[str]) -> dict[str, ContentIndexStatus]:
        with self.storage.connect() as connection:
            return self.storage.states_by_paths(connection, paths)

    def get_index_status(self) -> dict[str, int]:
        with self.storage.connect() as connection:
            return self.storage.status(connection)

    def _record_reason(
        self, connection: sqlite3.Connection, path: Path, reason: str
    ) -> None:
        try:
            stat = path.stat()
            size_bytes, mtime_ns = stat.st_size, stat.st_mtime_ns
        except OSError:
            size_bytes, mtime_ns = 0, 0
        state = (
            ContentIndexState.UNSUPPORTED
            if reason in {"unsupported_type", "binary", "invalid_utf8"}
            else ContentIndexState.UNAVAILABLE
        )
        self.storage.record_state(
            connection,
            path=str(path),
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            state=state,
            reason=reason,
        )
