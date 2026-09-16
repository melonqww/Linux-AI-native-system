"""First-party Security Center foundation worker.

Importing this package only defines contracts and lifecycle functions. It does
not perform I/O, allocate external resources, or start the worker.
"""

from __future__ import annotations

from collections.abc import Mapping
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
from .scanner import ByteSignature, FileScanner, HashSignature, SignatureDatabase


_started = False
_scanner: FileScanner | None = None
_campaign_scanner: CampaignScanner | None = None
_finding_store: FindingStore | None = None
_posture_collector: UbuntuPostureCollector | None = None


def worker_start(
    allowed_roots: Mapping[str, str | os.PathLike[str]] | None = None,
    *,
    signatures: SignatureDatabase | None = None,
    external_detectors: tuple[StreamDetector, ...] | None = None,
    finding_database: str | os.PathLike[str] | None = None,
    scan_profiles: Mapping[str, ScanProfile] | None = None,
    posture_collector: UbuntuPostureCollector | None = None,
) -> None:
    """Start with roots supplied only by trusted bootstrap code."""

    global _campaign_scanner, _finding_store, _posture_collector, _scanner, _started
    if _started:
        return
    roots = _roots_from_environment() if allowed_roots is None else allowed_roots
    detectors = (
        _detectors_from_environment()
        if external_detectors is None
        else external_detectors
    )
    signature_database = signatures or SignatureDatabase.builtin()
    scanner = FileScanner(
        roots,
        signatures=signature_database,
        external_detectors=detectors,
    )
    database = (
        os.environ.get("AI_NATIVE_SECURITY_FINDINGS_DATABASE", ":memory:")
        if finding_database is None
        else finding_database
    )
    _validate_finding_database_boundary(database, roots)
    finding_store = FindingStore(database)
    campaign_options = {} if scan_profiles is None else {"profiles": scan_profiles}
    try:
        campaign_scanner = CampaignScanner(
            roots,
            scanner.scan,
            finding_store.record,
            **campaign_options,
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
        if set(payload) != {"resource_id", "mode"}:
            raise ValueError("invalid_payload")
        assert _campaign_scanner is not None
        return _campaign_scanner.run(payload["resource_id"], payload["mode"])
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
    raise ValueError("unknown_operation")


def worker_stop() -> None:
    """Stop the worker; repeated stops are harmless."""

    global _campaign_scanner, _finding_store, _posture_collector, _scanner, _started
    if _finding_store is not None:
        _finding_store.close()
    _finding_store = None
    _campaign_scanner = None
    _posture_collector = None
    _scanner = None
    _started = False


def _require_started() -> None:
    if not _started:
        raise RuntimeError("security_worker_not_started")


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


def _validate_finding_database_boundary(
    database: str | os.PathLike[str],
    roots: Mapping[str, str | os.PathLike[str]],
) -> None:
    if str(database) == ":memory:":
        return
    database_path = Path(database).expanduser().absolute()
    for raw_root in roots.values():
        root = Path(raw_root).expanduser().resolve(strict=True)
        try:
            database_path.relative_to(root)
        except ValueError:
            continue
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
