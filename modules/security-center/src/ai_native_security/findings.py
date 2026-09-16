"""Durable, metadata-only finding storage for Security Center."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
from typing import Callable, Iterator

from .contracts import DetectorObservation, ScanResult


FINDING_SCHEMA_VERSION = 1
MAX_FINDINGS_PAGE = 25
_STATES = frozenset({"active", "resolved", "ignored"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class FindingStoreError(RuntimeError):
    """The private finding database could not be used safely."""


class FindingStore:
    """SQLite store that never persists file content or absolute scan paths."""

    def __init__(
        self,
        database: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._memory_connection: sqlite3.Connection | None = None
        if str(database) == ":memory:":
            self._database: Path | str = ":memory:"
            self._memory_connection = sqlite3.connect(":memory:", timeout=2.0)
        else:
            self._database = Path(database).expanduser().absolute()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        try:
            if isinstance(self._database, Path):
                self._database.parent.mkdir(parents=True, exist_ok=True)
            with self._connection() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS security_findings (
                        finding_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        resource_id TEXT NOT NULL,
                        relative_path TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        detector TEXT NOT NULL,
                        detector_version TEXT NOT NULL,
                        rule_id TEXT NOT NULL,
                        classification TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        state TEXT NOT NULL DEFAULT 'active',
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        occurrence_count INTEGER NOT NULL DEFAULT 1,
                        UNIQUE(resource_id, relative_path, sha256, detector,
                               detector_version, rule_id)
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_security_findings_recent "
                    "ON security_findings(state, last_seen_at DESC, finding_id DESC)"
                )
            self._harden_database_files()
        except (OSError, sqlite3.Error) as error:
            raise FindingStoreError("finding_store_unavailable") from error

    def record(self, result: ScanResult) -> tuple[int, ...]:
        """Upsert confirmed observations and return their stable finding IDs."""

        if result.verdict != "malware_detected" or not result.observations:
            return ()
        if result.sha256 is None or result.size_bytes is None:
            raise FindingStoreError("invalid_finding_source")
        seen_at = self._timestamp()
        finding_ids: list[int] = []
        try:
            with self._connection() as connection:
                for observation in result.observations:
                    detector_version = _detector_version(result, observation)
                    self._upsert(
                        connection, result, observation, detector_version, seen_at
                    )
                    row = connection.execute(
                        """
                        SELECT finding_id FROM security_findings
                        WHERE resource_id = ? AND relative_path = ? AND sha256 = ?
                          AND detector = ? AND detector_version = ? AND rule_id = ?
                        """,
                        (
                            result.resource_id,
                            result.relative_path,
                            result.sha256,
                            observation.detector,
                            detector_version,
                            observation.rule_id,
                        ),
                    ).fetchone()
                    if row is None:
                        raise FindingStoreError("finding_store_write_failed")
                    finding_ids.append(int(row[0]))
        except (OSError, sqlite3.Error) as error:
            raise FindingStoreError("finding_store_unavailable") from error
        self._harden_database_files()
        return tuple(finding_ids)

    def list(self, *, state: str = "active", limit: int = 20) -> dict[str, object]:
        if state not in _STATES:
            raise ValueError("invalid_finding_state")
        if type(limit) is not int or not 1 <= limit <= MAX_FINDINGS_PAGE:
            raise ValueError("invalid_finding_limit")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT finding_id, resource_id, relative_path, sha256, size_bytes,
                           detector, detector_version, rule_id, classification,
                           severity, state,
                           first_seen_at, last_seen_at, occurrence_count
                    FROM security_findings
                    WHERE state = ?
                    ORDER BY last_seen_at DESC, finding_id DESC
                    LIMIT ?
                    """,
                    (state, limit),
                ).fetchall()
        except (OSError, sqlite3.Error) as error:
            raise FindingStoreError("finding_store_unavailable") from error
        return {
            "schema_version": FINDING_SCHEMA_VERSION,
            "state": state,
            "findings": [self._serialize(row) for row in rows],
        }

    def get(self, finding_id: object) -> dict[str, object]:
        if type(finding_id) is not int or finding_id < 1:
            raise ValueError("invalid_finding_id")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT finding_id, resource_id, relative_path, sha256, size_bytes,
                           detector, detector_version, rule_id, classification,
                           severity, state, first_seen_at, last_seen_at,
                           occurrence_count
                    FROM security_findings WHERE finding_id = ?
                    """,
                    (finding_id,),
                ).fetchone()
        except (OSError, sqlite3.Error) as error:
            raise FindingStoreError("finding_store_unavailable") from error
        if row is None:
            raise ValueError("finding_not_found")
        return self._serialize(row)

    def set_state(self, finding_id: int, state: str) -> None:
        if state not in _STATES:
            raise ValueError("invalid_finding_state")
        try:
            with self._connection() as connection:
                changed = connection.execute(
                    "UPDATE security_findings SET state = ? WHERE finding_id = ?",
                    (state, finding_id),
                ).rowcount
        except (OSError, sqlite3.Error) as error:
            raise FindingStoreError("finding_store_unavailable") from error
        if changed != 1:
            raise ValueError("finding_not_found")

    def close(self) -> None:
        """Connections are short-lived; retained for a symmetric worker lifecycle."""

        if self._memory_connection is not None:
            self._memory_connection.close()
            self._memory_connection = None

    def _connect(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            return self._memory_connection
        connection = sqlite3.connect(self._database, timeout=2.0)
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            if connection is not self._memory_connection:
                connection.close()

    def _timestamp(self) -> str:
        current = self._clock()
        if current.tzinfo is None:
            raise FindingStoreError("invalid_finding_clock")
        return current.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )

    @staticmethod
    def _upsert(
        connection: sqlite3.Connection,
        result: ScanResult,
        observation: DetectorObservation,
        detector_version: str,
        seen_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO security_findings (
                resource_id, relative_path, sha256, size_bytes, detector,
                detector_version, rule_id, classification, severity, state,
                first_seen_at, last_seen_at, occurrence_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, 1)
            ON CONFLICT(resource_id, relative_path, sha256, detector,
                        detector_version, rule_id)
            DO UPDATE SET
                classification = excluded.classification,
                severity = excluded.severity,
                state = 'active',
                last_seen_at = excluded.last_seen_at,
                occurrence_count = security_findings.occurrence_count + 1
            """,
            (
                result.resource_id,
                result.relative_path,
                result.sha256,
                result.size_bytes,
                observation.detector,
                detector_version,
                observation.rule_id,
                observation.classification,
                observation.severity,
                seen_at,
                seen_at,
            ),
        )

    @staticmethod
    def _serialize(row: tuple[object, ...]) -> dict[str, object]:
        if (
            len(row) != 14
            or type(row[0]) is not int
            or row[0] <= 0
            or not _safe_text(row[1], 64, ascii_only=True)
            or not _safe_relative_path(row[2])
            or type(row[3]) is not str
            or _SHA256.fullmatch(row[3]) is None
            or type(row[4]) is not int
            or row[4] < 0
            or any(
                not _safe_text(row[index], 48, ascii_only=True)
                for index in (5, 6, 7, 8, 9)
            )
            or row[10] not in _STATES
            or any(not _safe_text(row[index], 32, ascii_only=True) for index in (11, 12))
            or type(row[13]) is not int
            or row[13] < 1
        ):
            raise FindingStoreError("invalid_finding_record")
        return {
            "finding_id": row[0],
            "resource_id": row[1],
            "relative_path": row[2],
            "sha256": row[3],
            "size_bytes": row[4],
            "detector": row[5],
            "detector_version": row[6],
            "rule_id": row[7],
            "classification": row[8],
            "severity": row[9],
            "state": row[10],
            "first_seen_at": row[11],
            "last_seen_at": row[12],
            "occurrence_count": row[13],
        }

    def _harden_database_files(self) -> None:
        if os.name == "nt" or not isinstance(self._database, Path):
            return
        for candidate in (
            self._database,
            self._database.with_name(self._database.name + "-wal"),
            self._database.with_name(self._database.name + "-shm"),
        ):
            try:
                candidate.chmod(0o600)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise FindingStoreError("finding_store_permissions_failed") from error


def _safe_text(value: object, maximum: int, *, ascii_only: bool) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= maximum
        and (not ascii_only or value.isascii())
    )


def _safe_relative_path(value: object) -> bool:
    if type(value) is not str or not 1 <= len(value) <= 1024 or "\\" in value:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def _detector_version(result: ScanResult, observation: DetectorObservation) -> str:
    status_name = (
        "local-signatures"
        if observation.detector in {"sha256-signature", "byte-signature"}
        else observation.detector
    )
    for detector in result.detectors:
        if detector.detector == status_name:
            return detector.version
    raise FindingStoreError("finding_detector_provenance_missing")
