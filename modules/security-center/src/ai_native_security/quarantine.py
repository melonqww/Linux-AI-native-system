"""Reversible, same-filesystem quarantine with durable receipts."""

from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Iterator, Mapping
from uuid import UUID, uuid4

from .findings import FindingStore
from .scanner import FileScanner, _is_junction, _normalize_relative_path, _reject_link_components


QUARANTINE_SCHEMA_VERSION = 1
_MAX_HASH_BYTES = 128 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STATES = frozenset(
    {"awaiting_confirmation", "committing", "quarantined", "restoring", "restored", "failed"}
)


class QuarantineManager:
    def __init__(
        self,
        allowed_roots: Mapping[str, str | os.PathLike[str]],
        scanner: FileScanner,
        findings: FindingStore,
        quarantine_root: str | os.PathLike[str],
        database: str | os.PathLike[str],
    ) -> None:
        self._roots = {
            resource_id: Path(root).resolve(strict=True)
            for resource_id, root in allowed_roots.items()
        }
        self._scanner = scanner
        self._findings = findings
        self._root = Path(quarantine_root).expanduser().absolute()
        self._objects = self._root / "objects"
        self._database = Path(database).expanduser().absolute()
        for scan_root in self._roots.values():
            for protected in (self._root, self._database):
                try:
                    protected.relative_to(scan_root)
                except ValueError:
                    continue
                raise ValueError("quarantine_inside_scan_root")
        self._objects.mkdir(parents=True, exist_ok=True)
        self._database.parent.mkdir(parents=True, exist_ok=True)
        if (
            self._root.is_symlink()
            or _is_junction(self._root)
            or self._objects.is_symlink()
            or _is_junction(self._objects)
            or self._database.is_symlink()
            or _is_junction(self._database)
        ):
            raise ValueError("unsafe_quarantine_storage")
        if self._database.exists() and not self._database.is_file():
            raise ValueError("unsafe_quarantine_storage")
        if os.name != "nt":
            self._root.chmod(0o700)
            self._objects.chmod(0o700)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quarantine_records (
                    quarantine_id TEXT PRIMARY KEY,
                    finding_id INTEGER NOT NULL,
                    resource_id TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    original_mode INTEGER NOT NULL,
                    object_name TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    error_code TEXT
                )
                """
            )
        self._harden_storage()

    def prepare(self, finding_id: object) -> dict[str, object]:
        finding = self._findings.get(finding_id)
        if finding["state"] != "active":
            raise ValueError("finding_not_active")
        resource_id = str(finding["resource_id"])
        relative_path = str(finding["relative_path"])
        result = self._scanner.scan(resource_id, relative_path)
        if result.sha256 != finding["sha256"] or result.verdict != "malware_detected":
            raise ValueError("finding_asset_changed")
        source = self._source_path(resource_id, relative_path)
        original_mode = stat.S_IMODE(source.stat(follow_symlinks=False).st_mode)
        quarantine_id = str(uuid4())
        object_name = quarantine_id + ".quarantine"
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO quarantine_records (
                    quarantine_id, finding_id, resource_id, relative_path,
                    sha256, size_bytes, original_mode, object_name, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'awaiting_confirmation')
                """,
                (
                    quarantine_id,
                    finding_id,
                    resource_id,
                    relative_path,
                    finding["sha256"],
                    finding["size_bytes"],
                    original_mode,
                    object_name,
                ),
            )
        return self._public(self._get(quarantine_id))

    def commit(self, quarantine_id: object) -> dict[str, object]:
        record = self._claim(quarantine_id, "awaiting_confirmation", "committing")
        try:
            source = self._source_path(record["resource_id"], record["relative_path"])
        except ValueError:
            return self._failed(record, "quarantine_source_unavailable")
        scan = self._scanner.scan(record["resource_id"], record["relative_path"])
        if scan.sha256 != record["sha256"]:
            return self._failed(record, "finding_asset_changed")
        destination = self._object_path(record["object_name"])
        if os.path.lexists(destination):
            return self._failed(record, "quarantine_destination_exists")
        try:
            os.replace(source, destination)
        except OSError as error:
            code = "cross_device_quarantine_unsupported" if error.errno == errno.EXDEV else "quarantine_move_failed"
            return self._failed(record, code)
        try:
            if _hash_regular_file(destination) != record["sha256"]:
                self._rollback_move(destination, source)
                return self._failed(record, "quarantine_verification_failed")
            if os.name != "nt":
                destination.chmod(0o600)
            self._set_state(record["quarantine_id"], "quarantined", None)
            self._findings.set_state(record["finding_id"], "resolved")
        except (OSError, ValueError):
            self._rollback_move(destination, source)
            if os.name != "nt" and source.exists():
                source.chmod(record["original_mode"])
            return self._failed(record, "quarantine_verification_failed")
        return self._public(self._get(record["quarantine_id"]))

    def restore(self, quarantine_id: object) -> dict[str, object]:
        record = self._claim(quarantine_id, "quarantined", "restoring")
        source = self._object_path(record["object_name"])
        try:
            destination = self._restore_path(record["resource_id"], record["relative_path"])
        except ValueError:
            self._set_state(record["quarantine_id"], "quarantined", "restore_destination_unavailable")
            return self._public(self._get(record["quarantine_id"]))
        if os.path.lexists(destination):
            self._set_state(record["quarantine_id"], "quarantined", "restore_destination_exists")
            return self._public(self._get(record["quarantine_id"]))
        try:
            if _hash_regular_file(source) != record["sha256"]:
                self._set_state(record["quarantine_id"], "quarantined", "quarantine_object_changed")
                return self._public(self._get(record["quarantine_id"]))
            os.replace(source, destination)
            if os.name != "nt":
                destination.chmod(record["original_mode"])
            if _hash_regular_file(destination) != record["sha256"]:
                self._rollback_move(destination, source)
                self._set_state(record["quarantine_id"], "quarantined", "restore_verification_failed")
                return self._public(self._get(record["quarantine_id"]))
            self._set_state(record["quarantine_id"], "restored", None)
            self._findings.set_state(record["finding_id"], "active")
        except OSError:
            self._set_state(record["quarantine_id"], "quarantined", "restore_move_failed")
        return self._public(self._get(record["quarantine_id"]))

    def close(self) -> None:
        return None

    def _source_path(self, resource_id: str, relative_path: str) -> Path:
        root = self._roots.get(resource_id)
        normalized = _normalize_relative_path(relative_path)
        if root is None or not normalized:
            raise ValueError("quarantine_source_unavailable")
        lexical = root.joinpath(*normalized.split("/"))
        try:
            _reject_link_components(root, lexical)
            resolved = lexical.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError) as error:
            raise ValueError("quarantine_source_unavailable") from error
        if not resolved.is_file() or resolved.is_symlink() or _is_junction(resolved):
            raise ValueError("quarantine_source_unavailable")
        return resolved

    def _restore_path(self, resource_id: str, relative_path: str) -> Path:
        root = self._roots.get(resource_id)
        normalized = _normalize_relative_path(relative_path)
        if root is None or not normalized:
            raise ValueError("restore_destination_unavailable")
        destination = root.joinpath(*normalized.split("/"))
        try:
            _reject_link_components(root, destination.parent)
            parent = destination.parent.resolve(strict=True)
            parent.relative_to(root)
        except (OSError, RuntimeError, ValueError) as error:
            raise ValueError("restore_destination_unavailable") from error
        return destination

    def _object_path(self, object_name: str) -> Path:
        if len(object_name) != 47 or not object_name.endswith(".quarantine"):
            raise ValueError("invalid_quarantine_object")
        UUID(object_name[:-11])
        return self._objects / object_name

    def _claim(self, quarantine_id: object, expected: str, claimed: str) -> dict[str, object]:
        identifier = _quarantine_id(quarantine_id)
        with self._connection() as connection:
            changed = connection.execute(
                "UPDATE quarantine_records SET state = ?, error_code = NULL "
                "WHERE quarantine_id = ? AND state = ?",
                (claimed, identifier, expected),
            ).rowcount
        if changed != 1:
            raise ValueError("invalid_quarantine_state")
        return self._get(identifier)

    def _failed(self, record: dict[str, object], code: str) -> dict[str, object]:
        self._set_state(str(record["quarantine_id"]), "failed", code)
        return self._public(self._get(str(record["quarantine_id"])))

    def _set_state(self, identifier: str, state: str, error: str | None) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE quarantine_records SET state = ?, error_code = ? WHERE quarantine_id = ?",
                (state, error, identifier),
            )

    def _get(self, quarantine_id: str) -> dict[str, object]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT quarantine_id, finding_id, resource_id, relative_path, sha256, "
                "size_bytes, original_mode, object_name, state, error_code "
                "FROM quarantine_records WHERE quarantine_id = ?",
                (quarantine_id,),
            ).fetchone()
        if row is None:
            raise ValueError("quarantine_not_found")
        keys = ("quarantine_id", "finding_id", "resource_id", "relative_path", "sha256", "size_bytes", "original_mode", "object_name", "state", "error_code")
        record = dict(zip(keys, row, strict=True))
        if (
            _quarantine_id(record["quarantine_id"]) != record["quarantine_id"]
            or type(record["finding_id"]) is not int
            or record["finding_id"] < 1
            or record["resource_id"] not in self._roots
            or _normalize_relative_path(record["relative_path"]) != record["relative_path"]
            or type(record["sha256"]) is not str
            or _SHA256.fullmatch(record["sha256"]) is None
            or type(record["size_bytes"]) is not int
            or record["size_bytes"] < 0
            or type(record["original_mode"]) is not int
            or not 0 <= record["original_mode"] <= 0o7777
            or record["object_name"] != record["quarantine_id"] + ".quarantine"
            or record["state"] not in _STATES
            or (
                record["error_code"] is not None
                and (
                    type(record["error_code"]) is not str
                    or not 1 <= len(record["error_code"]) <= 64
                    or not record["error_code"].isascii()
                )
            )
        ):
            raise ValueError("invalid_quarantine_record")
        return record

    @staticmethod
    def _public(record: dict[str, object]) -> dict[str, object]:
        return {
            "schema_version": QUARANTINE_SCHEMA_VERSION,
            "quarantine_id": record["quarantine_id"],
            "finding_id": record["finding_id"],
            "resource_id": record["resource_id"],
            "relative_path": record["relative_path"],
            "sha256": record["sha256"],
            "size_bytes": record["size_bytes"],
            "state": record["state"],
            "error_code": record["error_code"],
        }

    @staticmethod
    def _rollback_move(source: Path, destination: Path) -> None:
        if source.exists() and not os.path.lexists(destination):
            os.replace(source, destination)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database, timeout=2.0)
        connection.execute("PRAGMA busy_timeout=2000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _harden_storage(self) -> None:
        if os.name == "nt":
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


def _quarantine_id(value: object) -> str:
    if type(value) is not str:
        raise ValueError("invalid_quarantine_id")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError("invalid_quarantine_id") from error
    if str(parsed) != value:
        raise ValueError("invalid_quarantine_id")
    return value


def _hash_regular_file(path: Path) -> str:
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_HASH_BYTES:
        raise OSError("unsafe_quarantine_object")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(64 * 1024):
            digest.update(block)
    return digest.hexdigest()
