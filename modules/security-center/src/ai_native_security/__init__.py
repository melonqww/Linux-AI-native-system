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
from .detectors import ClamdUnixSocketDetector, StreamDetector
from .scanner import ByteSignature, FileScanner, HashSignature, SignatureDatabase


_started = False
_scanner: FileScanner | None = None


def worker_start(
    allowed_roots: Mapping[str, str | os.PathLike[str]] | None = None,
    *,
    signatures: SignatureDatabase | None = None,
    external_detectors: tuple[StreamDetector, ...] | None = None,
) -> None:
    """Start with roots supplied only by trusted bootstrap code."""

    global _scanner, _started
    if _started:
        return
    roots = _roots_from_environment() if allowed_roots is None else allowed_roots
    detectors = (
        _detectors_from_environment()
        if external_detectors is None
        else external_detectors
    )
    _scanner = FileScanner(
        roots,
        signatures=signatures,
        external_detectors=detectors,
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
        return _scanner.scan(payload["resource_id"], payload["relative_path"]).to_dict()
    raise ValueError("unknown_operation")


def worker_stop() -> None:
    """Stop the worker; repeated stops are harmless."""

    global _scanner, _started
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


__all__ = [
    "ByteSignature",
    "ClamdUnixSocketDetector",
    "DetectorObservation",
    "DetectorStatus",
    "FileScanner",
    "HashSignature",
    "ScanResult",
    "SecurityModuleStatus",
    "SignatureDatabase",
    "worker_health",
    "worker_invoke",
    "worker_start",
    "worker_stop",
]
