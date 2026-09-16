"""Immutable, bounded public contracts for Security Center."""

from __future__ import annotations

from dataclasses import dataclass, field


STATUS_SCHEMA_VERSION = 1
MODULE_ID = "security.center"
MODULE_VERSION = "0.5.0"
MODULE_LIFECYCLE = "on-demand"
STATUS_CAPABILITIES = (
    "security.module.status",
    "security.files.scan",
    "security.scan.run",
    "security.findings.list",
    "security.posture.scan",
)
SCAN_SCHEMA_VERSION = 2
MAX_OBSERVATIONS = 8
_DETECTORS = frozenset({"sha256-signature", "byte-signature", "clamd"})
_DETECTOR_STATES = frozenset({"completed", "failed", "unavailable"})
_SEVERITIES = frozenset({"low", "medium", "high", "critical"})
_ERROR_CODES = frozenset(
    {
        "invalid_resource_id",
        "invalid_relative_path",
        "resource_not_available",
        "resource_not_accessible",
        "not_regular_file",
        "file_too_large",
        "scan_timeout",
        "scan_io_error",
        "file_changed_during_scan",
        "detector_unavailable",
        "detector_timeout",
        "detector_protocol_error",
        "detector_identity_rejected",
    }
)


@dataclass(frozen=True, slots=True)
class SecurityModuleStatus:
    """Public status returned by the foundation capability."""

    schema_version: int = field(init=False, default=STATUS_SCHEMA_VERSION)
    module_id: str = field(init=False, default=MODULE_ID)
    module_version: str = field(init=False, default=MODULE_VERSION)
    state: str = field(init=False, default="ready")
    lifecycle: str = field(init=False, default=MODULE_LIFECYCLE)
    capabilities: tuple[str, ...] = field(init=False, default=STATUS_CAPABILITIES)

    def to_dict(self) -> dict[str, object]:
        """Return a detached response containing JSON-native value types only."""

        return {
            "schema_version": self.schema_version,
            "module_id": self.module_id,
            "module_version": self.module_version,
            "state": self.state,
            "lifecycle": self.lifecycle,
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True, slots=True)
class DetectorObservation:
    """A bounded detector match; it never contains file content or host paths."""

    detector: str
    rule_id: str
    classification: str
    severity: str

    def __post_init__(self) -> None:
        if self.detector not in _DETECTORS:
            raise ValueError("invalid_detector")
        if not _bounded_ascii(self.rule_id) or not _bounded_ascii(self.classification):
            raise ValueError("invalid_observation_label")
        if self.severity not in _SEVERITIES:
            raise ValueError("invalid_observation_severity")

    def to_dict(self) -> dict[str, str]:
        return {
            "detector": self.detector,
            "rule_id": self.rule_id,
            "classification": self.classification,
            "severity": self.severity,
        }


@dataclass(frozen=True, slots=True)
class DetectorStatus:
    """Bounded coverage state for one trusted detector."""

    detector: str
    version: str
    state: str
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.detector not in {"local-signatures", "clamd"}:
            raise ValueError("invalid_detector_status")
        if not _bounded_ascii(self.version):
            raise ValueError("invalid_detector_version")
        if self.state not in _DETECTOR_STATES:
            raise ValueError("invalid_detector_state")
        if self.state == "completed":
            if self.reason_code is not None:
                raise ValueError("completed_detector_has_reason")
        elif self.reason_code not in _ERROR_CODES:
            raise ValueError("invalid_detector_reason")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "detector": self.detector,
            "version": self.version,
            "state": self.state,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Public, JSON-safe result for one bounded file scan."""

    resource_id: str
    relative_path: str
    status: str
    verdict: str
    error_code: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    observations: tuple[DetectorObservation, ...] = ()
    detectors: tuple[DetectorStatus, ...] = ()
    schema_version: int = field(init=False, default=SCAN_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        if not isinstance(self.resource_id, str) or len(self.resource_id) > 64:
            raise ValueError("invalid_result_resource")
        if not isinstance(self.relative_path, str) or len(self.relative_path) > 1024:
            raise ValueError("invalid_result_path")
        if len(self.observations) > MAX_OBSERVATIONS:
            raise ValueError("too_many_observations")
        if len(self.detectors) > 8:
            raise ValueError("too_many_detectors")
        if len({item.detector for item in self.detectors}) != len(self.detectors):
            raise ValueError("duplicate_detector_status")
        if self.detectors and self.detectors[0].detector != "local-signatures":
            raise ValueError("missing_local_detector_status")
        if self.status == "completed":
            if self.verdict not in {"malware_detected", "no_threat_detected"}:
                raise ValueError("invalid_completed_verdict")
            if self.error_code is not None or self.sha256 is None or self.size_bytes is None:
                raise ValueError("incomplete_completed_result")
            _validate_file_measurements(self.sha256, self.size_bytes)
            if (self.verdict == "malware_detected") != bool(self.observations):
                raise ValueError("observations_verdict_mismatch")
            if not self.detectors or any(
                item.state != "completed" for item in self.detectors
            ):
                raise ValueError("incomplete_detector_coverage")
        elif self.status == "partial":
            if self.verdict not in {"malware_detected", "unknown"}:
                raise ValueError("invalid_partial_verdict")
            if (
                self.error_code not in _ERROR_CODES
                or self.sha256 is None
                or self.size_bytes is None
                or not self.detectors
                or all(item.state == "completed" for item in self.detectors)
            ):
                raise ValueError("invalid_partial_result")
            _validate_file_measurements(self.sha256, self.size_bytes)
            if not any(
                item.reason_code == self.error_code for item in self.detectors
            ):
                raise ValueError("partial_reason_mismatch")
            if (self.verdict == "malware_detected") != bool(self.observations):
                raise ValueError("partial_observations_verdict_mismatch")
        elif self.status in {"rejected", "failed"}:
            if (
                self.verdict != "unknown"
                or self.error_code not in _ERROR_CODES
                or self.sha256 is not None
                or self.size_bytes is not None
                or self.observations
                or self.detectors
            ):
                raise ValueError("invalid_incomplete_result")
        else:
            raise ValueError("invalid_scan_status")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "resource_id": self.resource_id,
            "relative_path": self.relative_path,
            "status": self.status,
            "verdict": self.verdict,
            "error_code": self.error_code,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "observations": [item.to_dict() for item in self.observations],
            "detectors": [item.to_dict() for item in self.detectors],
        }


def _bounded_ascii(value: object) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 48 and value.isascii()


def _validate_file_measurements(digest: str, size: int) -> None:
    if (
        type(digest) is not str
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ValueError("invalid_result_hash")
    if type(size) is not int or size < 0:
        raise ValueError("invalid_result_size")
