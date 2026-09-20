"""Durable background scan jobs with bounded progress and opt-in scheduling."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Iterator
from uuid import UUID, uuid4


JOB_SCHEMA_VERSION = 1
_FINAL_STATES = frozenset({"completed", "partial", "cancelled", "failed"})


class SecurityJobManager:
    def __init__(
        self,
        database: str | Path,
        runner: Callable[
            [dict[str, object], Callable[[dict[str, int]], None], Callable[[], bool]],
            dict[str, object],
        ],
        *,
        automatic_payload: Mapping[str, object] | None = None,
        automatic_interval_seconds: int = 24 * 60 * 60,
    ) -> None:
        if not 60 <= automatic_interval_seconds <= 31 * 24 * 60 * 60:
            raise ValueError("invalid_automatic_scan_interval")
        self._database = Path(database).expanduser().absolute()
        self._database.parent.mkdir(parents=True, exist_ok=True)
        self._runner = runner
        self._automatic_payload = (
            None if automatic_payload is None else dict(automatic_payload)
        )
        self._automatic_interval = automatic_interval_seconds
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, object]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._active_job_id: str | None = None
        self._closed = threading.Event()
        self._initialize_database()
        self._scheduler = threading.Thread(
            target=self._scheduler_loop,
            name="security-scan-scheduler",
            daemon=True,
        )
        self._scheduler.start()

    def start(self, payload: dict[str, object], *, automatic: bool = False) -> dict[str, object]:
        if type(payload) is not dict:
            raise ValueError("invalid_scan_job")
        target = payload.get("target")
        mode = payload.get("mode")
        if target not in {"quick", "full", "file", "folder"}:
            raise ValueError("invalid_scan_job")
        if mode not in {"quick", "full", "single"}:
            raise ValueError("invalid_scan_job")
        with self._lock:
            if self._active_job_id is not None:
                active = self._jobs.get(self._active_job_id)
                if active is not None and active["state"] not in _FINAL_STATES:
                    raise ValueError("scan_job_already_running")
            now = time.time()
            job_id = str(uuid4())
            job: dict[str, object] = {
                "schema_version": JOB_SCHEMA_VERSION,
                "job_id": job_id,
                "target": target,
                "mode": mode,
                "automatic": automatic,
                "state": "queued",
                "verdict": "unknown",
                "scanned_files": 0,
                "scanned_bytes": 0,
                "threat_files": 0,
                "unknown_files": 0,
                "skipped_files": 0,
                "created_at": self._timestamp(now),
                "started_at": None,
                "finished_at": None,
                "elapsed_seconds": 0,
                "error_code": None,
                "_created_epoch": now,
                "_started_epoch": None,
            }
            self._jobs[job_id] = job
            self._cancel_events[job_id] = threading.Event()
            self._active_job_id = job_id
            self._persist(job)
            threading.Thread(
                target=self._run,
                args=(job_id, dict(payload)),
                name=f"security-scan-{job_id[:8]}",
                daemon=True,
            ).start()
            return self._snapshot(job)

    def status(self, job_id: object = None) -> dict[str, object]:
        if job_id is not None:
            self._validate_job_id(job_id)
        with self._lock:
            selected = (
                self._jobs.get(job_id)
                if job_id is not None
                else self._jobs.get(self._active_job_id or "")
            )
            if selected is not None:
                return self._snapshot(selected)
        if job_id is None:
            return {"schema_version": JOB_SCHEMA_VERSION, "job": None}
        loaded = self._load(job_id)
        if loaded is None:
            raise ValueError("scan_job_not_found")
        return loaded

    def cancel(self, job_id: object) -> dict[str, object]:
        self._validate_job_id(job_id)
        assert isinstance(job_id, str)
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError("scan_job_not_found")
            if job["state"] in _FINAL_STATES:
                return self._snapshot(job)
            self._cancel_events[job_id].set()
            job["state"] = "cancelling"
            return self._snapshot(job)

    def history(self, limit: object = 10) -> dict[str, object]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("invalid_scan_job_limit")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM security_scan_jobs ORDER BY created_epoch DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return {
            "schema_version": JOB_SCHEMA_VERSION,
            "jobs": [json.loads(row[0]) for row in rows],
        }

    def settings(self) -> dict[str, object]:
        enabled, next_epoch = self._read_settings()
        return {
            "schema_version": JOB_SCHEMA_VERSION,
            "automatic_scans_enabled": enabled,
            "frequency_hours": self._automatic_interval // 3600,
            "next_scan_at": self._timestamp(next_epoch) if next_epoch else None,
        }

    def update_settings(self, enabled: object) -> dict[str, object]:
        if type(enabled) is not bool:
            raise ValueError("invalid_automatic_scan_setting")
        next_epoch = time.time() + self._automatic_interval if enabled else None
        with self._connection() as connection:
            connection.execute(
                "UPDATE security_scan_settings "
                "SET enabled = ?, next_epoch = ? WHERE singleton = 1",
                (int(enabled), next_epoch),
            )
        return self.settings()

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            for event in self._cancel_events.values():
                event.set()
        self._scheduler.join(timeout=1)

    def _run(self, job_id: str, payload: dict[str, object]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            started = time.time()
            job["state"] = "running"
            job["started_at"] = self._timestamp(started)
            job["_started_epoch"] = started
            self._persist(job)

        def progress(values: dict[str, int]) -> None:
            with self._lock:
                current = self._jobs[job_id]
                for field in (
                    "scanned_files",
                    "scanned_bytes",
                    "threat_files",
                    "unknown_files",
                    "skipped_files",
                ):
                    current[field] = max(0, int(values.get(field, current[field])))

        try:
            result = self._runner(
                payload,
                progress,
                self._cancel_events[job_id].is_set,
            )
            if not isinstance(result, dict):
                raise RuntimeError("invalid_scan_job_result")
            with self._lock:
                job = self._jobs[job_id]
                for field in (
                    "verdict",
                    "scanned_files",
                    "scanned_bytes",
                    "threat_files",
                    "unknown_files",
                    "skipped_files",
                    "error_code",
                ):
                    if field in result:
                        job[field] = result[field]
                state = result.get("status")
                job["state"] = state if state in _FINAL_STATES else "failed"
                if self._cancel_events[job_id].is_set() and job["state"] != "completed":
                    job["state"] = "cancelled"
                    job["error_code"] = "scan_cancelled"
        except Exception:
            with self._lock:
                job = self._jobs[job_id]
                job["state"] = "failed"
                job["verdict"] = "unknown"
                job["error_code"] = "scan_job_failed"
        finally:
            with self._lock:
                job = self._jobs[job_id]
                finished = time.time()
                job["finished_at"] = self._timestamp(finished)
                started_epoch = job.get("_started_epoch")
                job["elapsed_seconds"] = max(
                    0, int(finished - float(started_epoch or finished))
                )
                self._persist(job)
                if self._active_job_id == job_id:
                    self._active_job_id = None
                self._cancel_events.pop(job_id, None)

    def _scheduler_loop(self) -> None:
        while not self._closed.wait(30):
            enabled, next_epoch = self._read_settings()
            if (
                not enabled
                or next_epoch is None
                or next_epoch > time.time()
                or self._automatic_payload is None
            ):
                continue
            try:
                self.start(dict(self._automatic_payload), automatic=True)
            except ValueError:
                continue
            with self._connection() as connection:
                connection.execute(
                    "UPDATE security_scan_settings SET next_epoch = ? WHERE singleton = 1",
                    (time.time() + self._automatic_interval,),
                )

    def _snapshot(self, job: Mapping[str, object]) -> dict[str, object]:
        result = {key: value for key, value in job.items() if not key.startswith("_")}
        started = job.get("_started_epoch")
        if started is not None and result["state"] not in _FINAL_STATES:
            result["elapsed_seconds"] = max(0, int(time.time() - float(started)))
        return result

    def _persist(self, job: Mapping[str, object]) -> None:
        payload = self._snapshot(job)
        with self._connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO security_scan_jobs"
                "(job_id, created_epoch, payload) VALUES (?, ?, ?)",
                (
                    job["job_id"],
                    job["_created_epoch"],
                    json.dumps(payload, separators=(",", ":")),
                ),
            )

    def _load(self, job_id: str) -> dict[str, object] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload FROM security_scan_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def _read_settings(self) -> tuple[bool, float | None]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT enabled, next_epoch FROM security_scan_settings WHERE singleton = 1"
            ).fetchone()
        return bool(row[0]), None if row[1] is None else float(row[1])

    def _initialize_database(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS security_scan_jobs ("
                "job_id TEXT PRIMARY KEY, created_epoch REAL NOT NULL, payload TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS security_scan_settings ("
                "singleton INTEGER PRIMARY KEY CHECK(singleton = 1), "
                "enabled INTEGER NOT NULL, next_epoch REAL)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO security_scan_settings(singleton, enabled, next_epoch) "
                "VALUES (1, 0, NULL)"
            )
            rows = connection.execute(
                "SELECT job_id, payload FROM security_scan_jobs"
            ).fetchall()
            for job_id, raw_payload in rows:
                try:
                    payload = json.loads(raw_payload)
                except (TypeError, ValueError):
                    continue
                if payload.get("state") not in {"queued", "running", "cancelling"}:
                    continue
                payload["state"] = "failed"
                payload["verdict"] = "unknown"
                payload["error_code"] = "scan_interrupted"
                payload["finished_at"] = self._timestamp(time.time())
                connection.execute(
                    "UPDATE security_scan_jobs SET payload = ? WHERE job_id = ?",
                    (json.dumps(payload, separators=(",", ":")), job_id),
                )
        if os.name == "posix":
            try:
                self._database.chmod(0o600)
            except OSError:
                pass

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database, timeout=2.0)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _timestamp(epoch: float) -> str:
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat()

    @staticmethod
    def _validate_job_id(job_id: object) -> None:
        if type(job_id) is not str:
            raise ValueError("invalid_scan_job_id")
        try:
            parsed = UUID(job_id)
        except (ValueError, AttributeError):
            raise ValueError("invalid_scan_job_id") from None
        if str(parsed) != job_id:
            raise ValueError("invalid_scan_job_id")
