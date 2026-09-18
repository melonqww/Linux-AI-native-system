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
    global _quarantine_manager, _scanner, _started
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
    if operation == "quarantine_prepare":
        if set(payload) != {"finding_id"}:
            raise ValueError("invalid_payload")
        return _require_quarantine().prepare(payload["finding_id"])
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
    global _quarantine_manager, _scanner, _started
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
