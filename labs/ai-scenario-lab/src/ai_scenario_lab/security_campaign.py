"""Deterministic external campaign for the production Security Center."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import time
from typing import Callable, Iterator

import ai_native_security as security
from ai_native_security.detectors import DetectorError


@dataclass(frozen=True, slots=True)
class SecurityCaseResult:
    case_id: str
    passed: bool
    duration_ms: float
    checks: tuple[str, ...]
    error: str | None = None
    linux_only: bool = False
    skipped: bool = False


class _SecurityCaseSkipped(RuntimeError):
    pass


class _UnavailableDetector:
    name = "clamd"
    version = "campaign-fault-v1"

    def begin(self, _deadline: float):
        raise DetectorError("detector_unavailable", unavailable=True)


class SecurityCampaignRunner:
    """Attack public production boundaries inside a disposable filesystem."""

    def __init__(self, project_root: Path, lab_root: Path) -> None:
        self.project_root = project_root.resolve(strict=True)
        self.lab_root = lab_root.resolve(strict=True)
        self.fixture = self._load_fixture()

    def run(self, *, run_id: str) -> tuple[Path, tuple[SecurityCaseResult, ...]]:
        report_dir = self.lab_root / "reports" / "security" / run_id
        report_dir.mkdir(parents=True, exist_ok=False)
        host = report_dir / "virtual-security-host"
        host.mkdir()
        canary = report_dir / "outside-canary.txt"
        canary.write_bytes(b"security-campaign-outside-canary-v1")
        canary_hash = _digest(canary)

        cases = (
            ("scan-and-findings", self._scan_and_findings),
            ("path-containment", self._path_containment),
            ("profile-limit-fail-closed", self._profile_limit),
            ("detector-failure-fail-closed", self._detector_failure),
            ("closed-request-contract", self._closed_request_contract),
            ("approval-withheld-no-effect", self._approval_withheld),
            ("quarantine-roundtrip-replay", self._quarantine_roundtrip),
            ("changed-after-prepare", self._changed_after_prepare),
            ("restore-collision", self._restore_collision),
            ("linux-storage-permissions", self._linux_permissions),
        )
        results = tuple(
            self._run_case(case_id, operation, host / case_id, canary, canary_hash)
            for case_id, operation in cases
        )
        self._write_report(report_dir, run_id, results, canary_hash == _digest(canary))
        latest = self.lab_root / "reports" / "security-latest.txt"
        latest.write_text(f"security/{run_id}", encoding="utf-8")
        return report_dir, results

    def _run_case(
        self,
        case_id: str,
        operation: Callable[[Path], tuple[str, ...]],
        case_root: Path,
        canary: Path,
        expected_canary_hash: str,
    ) -> SecurityCaseResult:
        started = time.monotonic()
        case_root.mkdir()
        try:
            checks = operation(case_root)
            if _digest(canary) != expected_canary_hash:
                raise AssertionError("outside_canary_changed")
            return SecurityCaseResult(
                case_id, True, (time.monotonic() - started) * 1000, checks
            )
        except _SecurityCaseSkipped as error:
            security.worker_stop()
            return SecurityCaseResult(
                case_id,
                False,
                (time.monotonic() - started) * 1000,
                (),
                str(error),
                linux_only=True,
                skipped=True,
            )
        except Exception as error:
            security.worker_stop()
            return SecurityCaseResult(
                case_id,
                False,
                (time.monotonic() - started) * 1000,
                (),
                f"{type(error).__name__}:{error}",
            )

    def _scan_and_findings(self, root: Path) -> tuple[str, ...]:
        clean = self.fixture["clean"].encode()
        threat = self.fixture["synthetic_threat"].encode()
        (root / "clean.txt").write_bytes(clean)
        (root / "synthetic.bin").write_bytes(threat)
        with self._worker(root, threat):
            clean_result = security.worker_invoke(
                "scan", {"resource_id": "fixture", "relative_path": "clean.txt"}
            )
            threat_result = security.worker_invoke(
                "scan", {"resource_id": "fixture", "relative_path": "synthetic.bin"}
            )
            security.worker_invoke(
                "scan", {"resource_id": "fixture", "relative_path": "synthetic.bin"}
            )
            findings = security.worker_invoke("findings_list", {})["findings"]
        assert clean_result["verdict"] == "no_threat_detected"
        assert threat_result["verdict"] == "malware_detected"
        assert threat_result["observations"][0]["rule_id"] == "campaign-synthetic"
        assert len(findings) == 1 and findings[0]["occurrence_count"] == 2
        assert str(root) not in json.dumps(findings)
        return ("clean verdict", "synthetic detection", "finding deduplication")

    def _path_containment(self, root: Path) -> tuple[str, ...]:
        (root / "inside.txt").write_bytes(b"inside")
        before = _digest(root / "inside.txt")
        with self._worker(root):
            traversal = security.worker_invoke(
                "scan", {"resource_id": "fixture", "relative_path": "../outside-canary.txt"}
            )
            absolute = security.worker_invoke(
                "scan", {"resource_id": "fixture", "relative_path": str(root / "inside.txt")}
            )
        assert traversal["status"] == "rejected"
        assert traversal["error_code"] == "invalid_relative_path"
        assert absolute["status"] == "rejected"
        assert _digest(root / "inside.txt") == before
        return ("traversal rejected", "absolute path rejected", "scan is read-only")

    def _profile_limit(self, root: Path) -> tuple[str, ...]:
        (root / "a.txt").write_bytes(b"a")
        (root / "b.txt").write_bytes(b"b")
        profiles = {
            name: security.ScanProfile(1, 1024, 1, 5) for name in ("quick", "full")
        }
        with self._worker(root, scan_profiles=profiles):
            result = security.worker_invoke(
                "scan_profile", {"resource_id": "fixture", "mode": "quick"}
            )
        assert result["status"] == "partial"
        assert result["verdict"] == "unknown"
        assert result["error_code"] == "profile_limit_reached"
        return ("bounded file count", "partial is never clean")

    def _detector_failure(self, root: Path) -> tuple[str, ...]:
        (root / "ordinary.bin").write_bytes(b"ordinary")
        with self._worker(root, external_detectors=(_UnavailableDetector(),)):
            result = security.worker_invoke(
                "scan", {"resource_id": "fixture", "relative_path": "ordinary.bin"}
            )
        assert result["status"] == "partial"
        assert result["verdict"] == "unknown"
        assert result["error_code"] == "detector_unavailable"
        return ("fault localized", "unavailable detector is not clean")

    def _closed_request_contract(self, root: Path) -> tuple[str, ...]:
        target = root / "ordinary.bin"
        target.write_bytes(b"ordinary")
        before = _digest(target)
        with self._worker(root):
            try:
                security.worker_invoke(
                    "scan",
                    {"resource_id": "fixture", "relative_path": "ordinary.bin", "extra": True},
                )
            except ValueError as error:
                assert str(error) == "invalid_payload"
            else:
                raise AssertionError("extra_field_accepted")
        assert _digest(target) == before
        return ("extra field rejected", "rejection has no file effect")

    def _approval_withheld(self, root: Path) -> tuple[str, ...]:
        threat = self.fixture["synthetic_threat"].encode()
        target = root / "threat.bin"
        target.write_bytes(threat)
        with self._worker(root, threat):
            finding = self._finding_for("threat.bin")
            prepared = security.worker_invoke(
                "quarantine_prepare", {"finding_id": finding}
            )
            assert prepared["state"] == "awaiting_confirmation"
            assert target.read_bytes() == threat
            assert not any(self._objects(root).iterdir())
        return ("prepare is non-mutating", "withheld approval leaves file in place")

    def _quarantine_roundtrip(self, root: Path) -> tuple[str, ...]:
        threat = self.fixture["synthetic_threat"].encode()
        target = root / "threat.bin"
        target.write_bytes(threat)
        with self._worker(root, threat):
            identifier = self._prepare("threat.bin")
            committed = security.worker_invoke(
                "quarantine_commit", {"quarantine_id": identifier}
            )
            assert committed["state"] == "quarantined" and not target.exists()
            try:
                security.worker_invoke("quarantine_commit", {"quarantine_id": identifier})
            except ValueError as error:
                assert str(error) == "invalid_quarantine_state"
            else:
                raise AssertionError("quarantine_replay_accepted")
            restored = security.worker_invoke(
                "quarantine_restore", {"quarantine_id": identifier}
            )
            assert restored["state"] == "restored"
            assert target.read_bytes() == threat
        return ("atomic quarantine", "commit replay rejected", "content restored")

    def _changed_after_prepare(self, root: Path) -> tuple[str, ...]:
        threat = self.fixture["synthetic_threat"].encode()
        target = root / "changing.bin"
        target.write_bytes(threat)
        with self._worker(root, threat):
            identifier = self._prepare("changing.bin")
            target.write_bytes(b"changed-after-preview")
            result = security.worker_invoke(
                "quarantine_commit", {"quarantine_id": identifier}
            )
        assert result["state"] == "failed"
        assert result["error_code"] == "finding_asset_changed"
        assert target.read_bytes() == b"changed-after-preview"
        return ("digest rechecked", "changed file retained")

    def _restore_collision(self, root: Path) -> tuple[str, ...]:
        threat = self.fixture["synthetic_threat"].encode()
        target = root / "collision.bin"
        target.write_bytes(threat)
        with self._worker(root, threat):
            identifier = self._prepare("collision.bin")
            security.worker_invoke("quarantine_commit", {"quarantine_id": identifier})
            target.write_bytes(b"new-unrelated-file")
            result = security.worker_invoke(
                "quarantine_restore", {"quarantine_id": identifier}
            )
        assert result["state"] == "quarantined"
        assert result["error_code"] == "restore_destination_exists"
        assert target.read_bytes() == b"new-unrelated-file"
        return ("restore does not overwrite", "quarantine object retained")

    def _linux_permissions(self, root: Path) -> tuple[str, ...]:
        if os.name == "nt":
            raise _SecurityCaseSkipped("requires Linux filesystem permissions")
        with self._worker(root):
            quarantine = root.parent / f"{root.name}-private"
            objects = quarantine / "objects"
            assert quarantine.stat().st_mode & 0o777 == 0o700
            assert objects.stat().st_mode & 0o777 == 0o700
        return ("quarantine root mode 0700", "object directory mode 0700")

    @contextmanager
    def _worker(
        self,
        root: Path,
        threat: bytes | None = None,
        **options,
    ) -> Iterator[None]:
        signatures = security.SignatureDatabase(
            hashes=(
                security.HashSignature(
                    rule_id="campaign-synthetic",
                    sha256=hashlib.sha256(threat or b"never-matches").hexdigest(),
                    classification="synthetic-test-marker",
                ),
            ),
            version="campaign-v1",
        )
        private = root.parent / f"{root.name}-private"
        security.worker_start(
            {"fixture": root},
            signatures=signatures,
            finding_database=private / "findings.sqlite3",
            quarantine_root=private,
            quarantine_database=private / "quarantine.sqlite3",
            **options,
        )
        try:
            yield
        finally:
            security.worker_stop()

    def _finding_for(self, relative_path: str) -> int:
        result = security.worker_invoke(
            "scan", {"resource_id": "fixture", "relative_path": relative_path}
        )
        assert result["verdict"] == "malware_detected"
        findings = security.worker_invoke("findings_list", {})["findings"]
        return int(findings[0]["finding_id"])

    def _prepare(self, relative_path: str) -> str:
        finding_id = self._finding_for(relative_path)
        prepared = security.worker_invoke(
            "quarantine_prepare", {"finding_id": finding_id}
        )
        assert prepared["state"] == "awaiting_confirmation"
        return str(prepared["quarantine_id"])

    @staticmethod
    def _objects(root: Path) -> Path:
        return root.parent / f"{root.name}-private" / "objects"

    def _load_fixture(self) -> dict[str, str]:
        path = self.lab_root / "security-fixtures" / "manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if set(payload) != {"schema_version", "clean", "synthetic_threat"}:
            raise ValueError("invalid_security_fixture")
        if payload["schema_version"] != 1 or any(
            type(payload[name]) is not str or not 1 <= len(payload[name]) <= 1024
            for name in ("clean", "synthetic_threat")
        ):
            raise ValueError("invalid_security_fixture")
        return payload

    def _write_report(
        self,
        directory: Path,
        run_id: str,
        results: tuple[SecurityCaseResult, ...],
        containment_passed: bool,
    ) -> None:
        passed = sum(item.passed for item in results)
        skipped = sum(item.skipped for item in results)
        failed = len(results) - passed - skipped
        summary = {
            "schema_version": 1,
            "campaign": "security-center",
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "environment": {
                "platform": platform.system(),
                "platform_release": platform.release(),
                "python": platform.python_version(),
            },
            "source_fingerprint": self._source_fingerprint(),
            "containment_passed": containment_passed,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "total": len(results),
            "cases": [asdict(item) for item in results],
        }
        (directory / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [
            "# Security Campaign report",
            "",
            f"- Result: `{passed}/{len(results)}`",
            f"- Skipped: `{skipped}`",
            f"- Containment canary: `{'PASS' if containment_passed else 'FAIL'}`",
            f"- Platform: `{platform.system()} {platform.release()}`",
            f"- Production fingerprint: `{summary['source_fingerprint']}`",
            "",
            "| Case | Result | Time | Evidence |",
            "|---|:---:|---:|---|",
        ]
        for item in results:
            evidence = "; ".join(item.checks) if item.passed else item.error
            state = "SKIP" if item.skipped else "PASS" if item.passed else "FAIL"
            lines.append(
                f"| `{item.case_id}` | {state} | "
                f"{item.duration_ms:.1f} ms | {evidence} |"
            )
        (directory / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _source_fingerprint(self) -> str:
        digest = hashlib.sha256()
        source = self.project_root / "modules" / "security-center" / "src"
        for path in sorted(source.rglob("*.py")):
            digest.update(path.relative_to(source).as_posix().encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = ["SecurityCampaignRunner", "SecurityCaseResult"]
