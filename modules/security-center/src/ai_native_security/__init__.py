"""First-party Security Center foundation worker.

Importing this package only defines contracts and lifecycle functions. It does
not perform I/O, allocate external resources, or start the worker.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
import json
import os
from pathlib import Path

from .contracts import (
    DetectorObservation,
    DetectorStatus,
    ScanResult,
    SecurityModuleStatus,
)
from .campaigns import CampaignScanner, ScanProfile
from .detectors import ClamdUnixSocketDetector, StreamDetector
from .findings import FindingStore, FindingStoreError
from .jobs import SecurityJobManager
from .posture import PostureObservation, UbuntuPostureCollector
from .quarantine import QuarantineManager
from .scanner import ByteSignature, FileScanner, HashSignature, SignatureDatabase
from .scope import normalize_exclusions, storage_is_excluded


_started = False
_scanner: FileScanner | None = None
_campaign_scanner: CampaignScanner | None = None
_finding_store: FindingStore | None = None
_posture_collector: UbuntuPostureCollector | None = None
_quarantine_manager: QuarantineManager | None = None
_job_manager: SecurityJobManager | None = None


def worker_start(
    allowed_roots: Mapping[str, str | os.PathLike[str]] | None = None,
    *,
    signatures: SignatureDatabase | None = None,
    external_detectors: tuple[StreamDetector, ...] | None = None,
    finding_database: str | os.PathLike[str] | None = None,
    scan_profiles: Mapping[str, ScanProfile] | None = None,
    posture_collector: UbuntuPostureCollector | None = None,
    quarantine_root: str | os.PathLike[str] | None = None,
    quarantine_database: str | os.PathLike[str] | None = None,
    excluded_paths: Mapping[str, Collection[str]] | None = None,
) -> None:
    """Start with roots supplied only by trusted bootstrap code."""

    global _campaign_scanner, _finding_store, _posture_collector
    global _job_manager, _quarantine_manager, _scanner, _started
    if _started:
        return
    roots = _roots_from_environment() if allowed_roots is None else allowed_roots
    detectors = (
        _detectors_from_environment()
        if external_detectors is None
        else external_detectors
    )
    signature_database = signatures or SignatureDatabase.builtin()
    resolved_roots = {
        key: Path(value).resolve(strict=True) for key, value in roots.items()
    }
    exclusions = (
        _exclusions_from_environment(roots)
        if excluded_paths is None
        else normalize_exclusions(resolved_roots, excluded_paths)
    )
    scanner = FileScanner(
        roots,
        signatures=signature_database,
        external_detectors=detectors,
        excluded_paths=exclusions,
    )
    database = (
        os.environ.get("AI_NATIVE_SECURITY_FINDINGS_DATABASE", ":memory:")
        if finding_database is None
        else finding_database
    )
    _validate_finding_database_boundary(database, roots, exclusions)
    finding_store = FindingStore(database)
    campaign_options = {} if scan_profiles is None else {"profiles": scan_profiles}
    try:
        campaign_scanner = CampaignScanner(
            roots,
            scanner.scan,
            finding_store.record,
            excluded_paths=exclusions,
            **campaign_options,
        )
        configured_root = quarantine_root or os.environ.get(
            "AI_NATIVE_SECURITY_QUARANTINE_ROOT"
        )
        configured_database = quarantine_database or os.environ.get(
            "AI_NATIVE_SECURITY_QUARANTINE_DATABASE"
        )
        if (configured_root is None) != (configured_database is None):
            raise ValueError("invalid_quarantine_configuration")
        quarantine_manager = (
            None
            if configured_root is None
            else QuarantineManager(
                roots,
                scanner,
                finding_store,
                configured_root,
                configured_database,
                excluded_paths=exclusions,
            )
        )
    except Exception:
        finding_store.close()
        raise
    _scanner = scanner
    _finding_store = finding_store
    _campaign_scanner = campaign_scanner
    _posture_collector = posture_collector or UbuntuPostureCollector(
        user_autostart=_user_autostart_from_environment(),
        signature_version=signature_database.version,
        clamd_configured=bool(detectors),
    )
    _quarantine_manager = quarantine_manager
    jobs_database = os.environ.get("AI_NATIVE_SECURITY_JOBS_DATABASE")
    _job_manager = (
        None
        if jobs_database is None
        else SecurityJobManager(
            jobs_database,
            _run_scan_job,
            automatic_payload={
                "target": "quick",
                "mode": "quick",
                "scopes": _quick_scopes_from_environment(roots),
            },
        )
    )
    _started = True


def worker_health() -> dict[str, object]:
    """Return bounded health details or fail closed while stopped."""

    _require_started()
    return {"status": "ready"}


def worker_invoke(operation: str, payload: dict[str, object]) -> dict[str, object]:
    """Invoke a closed, bounded Security Center operation."""

    if type(payload) is not dict:
        raise ValueError("invalid_payload")
    _require_started()
    if operation == "status":
        if payload:
            raise ValueError("invalid_payload")
        return SecurityModuleStatus().to_dict()
    if operation == "scan":
        if set(payload) != {"resource_id", "relative_path"}:
            raise ValueError("invalid_payload")
        assert _scanner is not None
        result = _scanner.scan(payload["resource_id"], payload["relative_path"])
        assert _finding_store is not None
        _finding_store.record(result)
        return result.to_dict()
    if operation == "scan_profile":
        if not {"resource_id", "mode"}.issubset(payload) or not set(payload).issubset(
            {"resource_id", "mode", "relative_path"}
        ):
            raise ValueError("invalid_payload")
        assert _campaign_scanner is not None
        return _campaign_scanner.run(
            payload["resource_id"],
            payload["mode"],
            payload.get("relative_path", ""),
        )
    if operation == "findings_list":
        if not set(payload).issubset({"state", "limit"}):
            raise ValueError("invalid_payload")
        assert _finding_store is not None
        return _finding_store.list(
            state=payload.get("state", "active"),
            limit=payload.get("limit", 20),
        )
    if operation == "posture_scan":
        if payload:
            raise ValueError("invalid_payload")
        assert _posture_collector is not None
        return _posture_collector.scan()
    if operation == "job_start":
        if not set(payload).issubset(
            {"target", "mode", "resource_id", "relative_path", "scopes"}
        ):
            raise ValueError("invalid_payload")
        return _require_jobs().start(payload)
    if operation == "job_status":
        if not set(payload).issubset({"job_id"}):
            raise ValueError("invalid_payload")
        return _require_jobs().status(payload.get("job_id"))
    if operation == "job_cancel":
        if set(payload) != {"job_id"}:
            raise ValueError("invalid_payload")
        return _require_jobs().cancel(payload["job_id"])
    if operation == "job_history":
        if not set(payload).issubset({"limit"}):
            raise ValueError("invalid_payload")
        return _require_jobs().history(payload.get("limit", 10))
    if operation == "job_settings_get":
        if payload:
            raise ValueError("invalid_payload")
        return _require_jobs().settings()
    if operation == "job_settings_update":
        if set(payload) != {"automatic_scans_enabled"}:
            raise ValueError("invalid_payload")
        return _require_jobs().update_settings(payload["automatic_scans_enabled"])
    if operation == "quarantine_prepare":
        if set(payload) != {"finding_id"}:
            raise ValueError("invalid_payload")
        return _require_quarantine().prepare(payload["finding_id"])
    if operation == "quarantine_list":
        if not set(payload).issubset({"state", "limit"}):
            raise ValueError("invalid_payload")
        return _require_quarantine().list(
            state=payload.get("state", "quarantined"),
            limit=payload.get("limit", 10),
        )
    if operation == "quarantine_cancel":
        if set(payload) != {"quarantine_id"}:
            raise ValueError("invalid_payload")
        return _require_quarantine().cancel(payload["quarantine_id"])
    if operation == "quarantine_commit":
        if set(payload) != {"quarantine_id"}:
            raise ValueError("invalid_payload")
        return _require_quarantine().commit(payload["quarantine_id"])
    if operation == "quarantine_restore":
        if set(payload) != {"quarantine_id"}:
            raise ValueError("invalid_payload")
        return _require_quarantine().restore(payload["quarantine_id"])
    raise ValueError("unknown_operation")


def worker_stop() -> None:
    """Stop the worker; repeated stops are harmless."""

    global _campaign_scanner, _finding_store, _posture_collector
    global _job_manager, _quarantine_manager, _scanner, _started
    if _job_manager is not None:
        _job_manager.close()
    _job_manager = None
    if _finding_store is not None:
        _finding_store.close()
    _finding_store = None
    _campaign_scanner = None
    _posture_collector = None
    if _quarantine_manager is not None:
        _quarantine_manager.close()
    _quarantine_manager = None
    _scanner = None
    _started = False


def _require_started() -> None:
    if not _started:
        raise RuntimeError("security_worker_not_started")


def _require_quarantine() -> QuarantineManager:
    if _quarantine_manager is None:
        raise RuntimeError("quarantine_not_configured")
    return _quarantine_manager


def _require_jobs() -> SecurityJobManager:
    if _job_manager is None:
        raise RuntimeError("security_jobs_not_configured")
    return _job_manager


def _run_scan_job(
    payload: dict[str, object],
    progress,
    cancelled,
) -> dict[str, object]:
    target = payload.get("target")
    mode = payload.get("mode")
    if target == "file":
        if set(payload) != {"target", "mode", "resource_id", "relative_path"}:
            raise ValueError("invalid_scan_job")
        assert _scanner is not None and _finding_store is not None
        result = _scanner.scan(payload["resource_id"], payload["relative_path"])
        _finding_store.record(result)
        was_cancelled = cancelled()
        summary = {
            "status": (
                "cancelled"
                if was_cancelled
                else "completed"
                if result.status == "completed"
                else "partial"
            ),
            "verdict": result.verdict if not was_cancelled else "unknown",
            "scanned_files": int(result.status == "completed"),
            "scanned_bytes": result.size_bytes or 0,
            "threat_files": int(result.verdict == "malware_detected"),
            "unknown_files": int(result.verdict == "unknown"),
            "skipped_files": int(result.status == "rejected"),
            "error_code": "scan_cancelled" if was_cancelled else result.error_code,
        }
        progress(
            {
                key: int(value)
                for key, value in summary.items()
                if key.endswith("files") or key == "scanned_bytes"
            }
        )
        return summary
    if target not in {"quick", "full", "folder"} or mode not in {"quick", "full"}:
        raise ValueError("invalid_scan_job")
    scopes = payload.get("scopes")
    if type(scopes) is not list or not 1 <= len(scopes) <= 32:
        raise ValueError("invalid_scan_job")
    totals = {
        "scanned_files": 0,
        "scanned_bytes": 0,
        "threat_files": 0,
        "unknown_files": 0,
        "skipped_files": 0,
    }
    status = "completed"
    error_code = None
    assert _campaign_scanner is not None
    for scope in scopes:
        if type(scope) is not dict or set(scope) != {"resource_id", "relative_path"}:
            raise ValueError("invalid_scan_job")
        base = dict(totals)

        def scoped_progress(values: dict[str, int]) -> None:
            progress({key: base[key] + values.get(key, 0) for key in totals})

        result = _campaign_scanner.run(
            scope["resource_id"],
            mode,
            scope["relative_path"],
            progress=scoped_progress,
            cancelled=cancelled,
        )
        for key in totals:
            totals[key] += int(result.get(key, 0))
        progress(totals)
        if result.get("status") == "cancelled":
            status = "cancelled"
            error_code = "scan_cancelled"
            break
        if result.get("status") != "completed":
            status = "partial"
            error_code = str(result.get("error_code") or "scan_incomplete")
    return {
        "status": status,
        "verdict": (
            "malware_detected"
            if totals["threat_files"]
            else "unknown"
            if status != "completed" or totals["unknown_files"]
            else "no_threat_detected"
        ),
        **totals,
        "error_code": error_code,
    }


def _roots_from_environment() -> dict[str, str]:
    encoded = os.environ.get("AI_NATIVE_SECURITY_SCAN_ROOTS", "{}")
    if len(encoded) > 16 * 1024:
        raise ValueError("invalid_scan_roots")
    try:
        parsed = json.loads(encoded)
    except json.JSONDecodeError as error:
        raise ValueError("invalid_scan_roots") from error
    if type(parsed) is not dict or len(parsed) > 32:
        raise ValueError("invalid_scan_roots")
    if not all(type(key) is str and type(value) is str for key, value in parsed.items()):
        raise ValueError("invalid_scan_roots")
    return parsed


def _detectors_from_environment() -> tuple[StreamDetector, ...]:
    raw_socket = os.environ.get("AI_NATIVE_SECURITY_CLAMD_SOCKET")
    raw_uids = os.environ.get("AI_NATIVE_SECURITY_CLAMD_PEER_UIDS")
    if raw_socket is None and raw_uids is None:
        return ()
    if raw_socket is None or raw_uids is None or len(raw_uids) > 256:
        raise ValueError("invalid_clamd_configuration")
    try:
        parsed_uids = json.loads(raw_uids)
    except json.JSONDecodeError as error:
        raise ValueError("invalid_clamd_configuration") from error
    if type(parsed_uids) is not list:
        raise ValueError("invalid_clamd_configuration")
    try:
        detector = ClamdUnixSocketDetector(
            socket_path=Path(raw_socket),
            expected_peer_uids=frozenset(parsed_uids),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("invalid_clamd_configuration") from error
    return (detector,)


def _exclusions_from_environment(
    roots: Mapping[str, str | os.PathLike[str]],
) -> dict[str, tuple[str, ...]]:
    encoded = os.environ.get("AI_NATIVE_SECURITY_EXCLUDED_PATHS", "{}")
    if len(encoded) > 16 * 1024:
        raise ValueError("invalid_scan_exclusions")
    try:
        parsed = json.loads(encoded)
    except json.JSONDecodeError as error:
        raise ValueError("invalid_scan_exclusions") from error
    resolved_roots = {
        key: Path(value).resolve(strict=True) for key, value in roots.items()
    }
    return normalize_exclusions(resolved_roots, parsed)


def _quick_scopes_from_environment(
    roots: Mapping[str, str | os.PathLike[str]],
) -> list[dict[str, str]]:
    encoded = os.environ.get("AI_NATIVE_SECURITY_QUICK_SCOPES", "[]")
    if len(encoded) > 16 * 1024:
        raise ValueError("invalid_quick_scan_scopes")
    try:
        parsed = json.loads(encoded)
    except json.JSONDecodeError as error:
        raise ValueError("invalid_quick_scan_scopes") from error
    if type(parsed) is not list or not 1 <= len(parsed) <= 32:
        raise ValueError("invalid_quick_scan_scopes")
    result: list[dict[str, str]] = []
    for item in parsed:
        if (
            type(item) is not dict
            or set(item) != {"resource_id", "relative_path"}
            or item["resource_id"] not in roots
            or type(item["relative_path"]) is not str
        ):
            raise ValueError("invalid_quick_scan_scopes")
        result.append(dict(item))
    return result


def _validate_finding_database_boundary(
    database: str | os.PathLike[str],
    roots: Mapping[str, str | os.PathLike[str]],
    exclusions: Mapping[str, tuple[str, ...]] | None = None,
) -> None:
    if str(database) == ":memory:":
        return
    database_path = Path(database).expanduser().absolute()
    normalized = normalize_exclusions(
        {key: Path(value).resolve(strict=True) for key, value in roots.items()},
        exclusions,
    )
    if not storage_is_excluded(database_path, roots, normalized):
        raise ValueError("finding_database_inside_scan_root")


def _user_autostart_from_environment() -> Path | None:
    raw = os.environ.get("AI_NATIVE_SECURITY_USER_AUTOSTART")
    if raw is None:
        return None
    if not 1 <= len(raw) <= 1024:
        raise ValueError("invalid_user_autostart_path")
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError("invalid_user_autostart_path")
    return path


__all__ = [
    "ByteSignature",
    "CampaignScanner",
    "ClamdUnixSocketDetector",
    "DetectorObservation",
    "DetectorStatus",
    "FileScanner",
    "FindingStore",
    "FindingStoreError",
    "HashSignature",
    "PostureObservation",
    "QuarantineManager",
    "ScanResult",
    "ScanProfile",
    "SecurityModuleStatus",
    "SignatureDatabase",
    "UbuntuPostureCollector",
    "worker_health",
    "worker_invoke",
    "worker_start",
    "worker_stop",
]
