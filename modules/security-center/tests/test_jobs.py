from __future__ import annotations

import json
import time
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from ai_native_security.jobs import SecurityJobManager


MODULE_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def job_root():
    root = MODULE_ROOT / f"security-jobs-{uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _wait(manager: SecurityJobManager, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = manager.status(job_id)
        if job["state"] in {"completed", "partial", "cancelled", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_background_job_reports_real_progress_and_history(job_root) -> None:
    def runner(_payload, progress, _cancelled):
        progress({"scanned_files": 2, "scanned_bytes": 12, "threat_files": 0})
        progress({"scanned_files": 4, "scanned_bytes": 30, "threat_files": 1})
        return {
            "status": "completed",
            "verdict": "malware_detected",
            "scanned_files": 4,
            "scanned_bytes": 30,
            "threat_files": 1,
            "unknown_files": 0,
            "skipped_files": 0,
            "error_code": None,
        }

    manager = SecurityJobManager(job_root / "jobs.sqlite3", runner)
    try:
        started = manager.start({"target": "full", "mode": "full"})
        completed = _wait(manager, started["job_id"])
        history = manager.history()
    finally:
        manager.close()

    assert completed["state"] == "completed"
    assert completed["scanned_files"] == 4
    assert completed["threat_files"] == 1
    assert history["jobs"][0]["job_id"] == started["job_id"]


def test_job_can_be_cancelled_and_only_one_can_run(job_root) -> None:
    def runner(_payload, progress, cancelled):
        scanned = 0
        while not cancelled():
            scanned += 1
            progress({"scanned_files": scanned})
            time.sleep(0.005)
        return {
            "status": "cancelled",
            "verdict": "unknown",
            "scanned_files": scanned,
            "scanned_bytes": 0,
            "threat_files": 0,
            "unknown_files": 0,
            "skipped_files": 0,
            "error_code": "scan_cancelled",
        }

    manager = SecurityJobManager(job_root / "jobs.sqlite3", runner)
    try:
        started = manager.start({"target": "quick", "mode": "quick"})
        try:
            manager.start({"target": "full", "mode": "full"})
        except ValueError as error:
            assert str(error) == "scan_job_already_running"
        else:
            raise AssertionError("second job was accepted")
        cancelling = manager.cancel(started["job_id"])
        completed = _wait(manager, started["job_id"])
    finally:
        manager.close()

    assert cancelling["state"] == "cancelling"
    assert completed["state"] == "cancelled"


def test_automatic_scans_are_opt_in_and_setting_is_durable(job_root) -> None:
    database = job_root / "jobs.sqlite3"
    manager = SecurityJobManager(database, lambda *_args: {})
    try:
        assert manager.settings()["automatic_scans_enabled"] is False
        enabled = manager.update_settings(True)
        assert enabled["automatic_scans_enabled"] is True
        assert enabled["next_scan_at"] is not None
    finally:
        manager.close()

    reopened = SecurityJobManager(database, lambda *_args: {})
    try:
        assert reopened.settings()["automatic_scans_enabled"] is True
    finally:
        reopened.close()


def test_interrupted_job_is_failed_when_manager_restarts(job_root) -> None:
    database = job_root / "jobs.sqlite3"
    manager = SecurityJobManager(database, lambda *_args: {})
    try:
        with manager._connection() as connection:
            payload = {
                "schema_version": 1,
                "job_id": "2afe508b-1234-4c8b-9b12-c52612345678",
                "state": "running",
                "verdict": "unknown",
                "finished_at": None,
                "error_code": None,
            }
            connection.execute(
                "INSERT INTO security_scan_jobs(job_id, created_epoch, payload) VALUES (?, ?, ?)",
                (payload["job_id"], time.time(), json.dumps(payload)),
            )
    finally:
        manager.close()

    reopened = SecurityJobManager(database, lambda *_args: {})
    try:
        restored = reopened.status(payload["job_id"])
    finally:
        reopened.close()

    assert restored["state"] == "failed"
    assert restored["error_code"] == "scan_interrupted"
    assert restored["finished_at"] is not None
