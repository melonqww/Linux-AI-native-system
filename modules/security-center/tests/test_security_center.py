import importlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import unittest
import uuid
from dataclasses import FrozenInstanceError
from unittest import mock

import ai_native_security as security
from ai_native_security import SecurityModuleStatus
from ai_native_security import (
    ByteSignature,
    DetectorObservation,
    HashSignature,
    SignatureDatabase,
)
from ai_native_security.detectors import DetectorError


EXPECTED_STATUS = {
    "schema_version": 1,
    "module_id": "security.center",
    "module_version": "0.7.0",
    "state": "ready",
    "lifecycle": "on-demand",
    "capabilities": [
        "security.module.status",
        "security.files.scan",
        "security.scan.run",
        "security.findings.list",
        "security.posture.scan",
        "security.quarantine.prepare",
        "security.quarantine.commit",
        "security.quarantine.restore",
    ],
}
MODULE_ROOT = Path(__file__).resolve().parents[1]


class SecurityCenterWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        security.worker_stop()

    def tearDown(self) -> None:
        security.worker_stop()

    def test_import_does_not_start_worker(self) -> None:
        importlib.reload(security)

        with self.assertRaisesRegex(RuntimeError, "security_worker_not_started"):
            security.worker_health()
        with self.assertRaisesRegex(RuntimeError, "security_worker_not_started"):
            security.worker_invoke("status", {})

    def test_lifecycle_exposes_health_and_status_only_after_start(self) -> None:
        security.worker_start()

        self.assertEqual(security.worker_health(), {"status": "ready"})
        self.assertEqual(security.worker_invoke("status", {}), EXPECTED_STATUS)

        security.worker_stop()
        with self.assertRaisesRegex(RuntimeError, "security_worker_not_started"):
            security.worker_health()

    def test_repeated_start_and_stop_are_idempotent(self) -> None:
        security.worker_start()
        security.worker_start()
        self.assertEqual(security.worker_invoke("status", {}), EXPECTED_STATUS)

        security.worker_stop()
        security.worker_stop()
        with self.assertRaisesRegex(RuntimeError, "security_worker_not_started"):
            security.worker_invoke("status", {})

    def test_unknown_operation_is_rejected(self) -> None:
        security.worker_start()

        with self.assertRaisesRegex(ValueError, "unknown_operation"):
            security.worker_invoke("unsupported", {})

    def test_posture_payload_is_closed(self) -> None:
        security.worker_start()

        with self.assertRaisesRegex(ValueError, "invalid_payload"):
            security.worker_invoke("posture_scan", {"command": "ufw disable"})

    def test_non_empty_and_non_object_payloads_are_rejected(self) -> None:
        security.worker_start()

        for payload in ({"unexpected": True}, [], None, ""):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "invalid_payload"):
                    security.worker_invoke("status", payload)  # type: ignore[arg-type]

    def test_status_contract_is_immutable_bounded_and_json_safe(self) -> None:
        status = SecurityModuleStatus()

        with self.assertRaises(TypeError):
            SecurityModuleStatus(state="forged")  # type: ignore[call-arg]
        with self.assertRaises(FrozenInstanceError):
            status.state = "stopped"  # type: ignore[misc]
        output = status.to_dict()
        self.assertEqual(output, EXPECTED_STATUS)
        self.assertLessEqual(len(json.dumps(output, sort_keys=True)), 512)
        self.assertEqual(json.loads(json.dumps(output)), EXPECTED_STATUS)


class SecurityFileScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        security.worker_stop()
        self.root = MODULE_ROOT / f"security-scan-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.findings_database = self.root.parent / f"{self.root.name}-findings.sqlite3"
        self.quarantine_root = self.root.parent / f"{self.root.name}-quarantine"
        self.quarantine_database = self.root.parent / f"{self.root.name}-quarantine.sqlite3"

    def tearDown(self) -> None:
        security.worker_stop()
        shutil.rmtree(self.root, ignore_errors=True)
        self.findings_database.unlink(missing_ok=True)
        self.findings_database.with_name(self.findings_database.name + "-wal").unlink(missing_ok=True)
        self.findings_database.with_name(self.findings_database.name + "-shm").unlink(missing_ok=True)
        shutil.rmtree(self.quarantine_root, ignore_errors=True)
        self.quarantine_database.unlink(missing_ok=True)
        self.quarantine_database.with_name(self.quarantine_database.name + "-wal").unlink(missing_ok=True)
        self.quarantine_database.with_name(self.quarantine_database.name + "-shm").unlink(missing_ok=True)

    def _start(
        self,
        *,
        signatures: SignatureDatabase | None = None,
        external_detectors: tuple[object, ...] = (),
        scan_profiles: dict[str, object] | None = None,
    ) -> None:
        security.worker_start(
            {"downloads": self.root},
            signatures=signatures,
            external_detectors=external_detectors,  # type: ignore[arg-type]
            finding_database=self.findings_database,
            scan_profiles=scan_profiles,  # type: ignore[arg-type]
            quarantine_root=self.quarantine_root,
            quarantine_database=self.quarantine_database,
        )

    def _scan(self, relative_path: object) -> dict[str, object]:
        return security.worker_invoke(
            "scan", {"resource_id": "downloads", "relative_path": relative_path}
        )

    def test_empty_or_malformed_signature_database_is_rejected_at_startup(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty_signature_database"):
            SignatureDatabase()
        with self.assertRaisesRegex(ValueError, "invalid_signature_hash"):
            SignatureDatabase(
                hashes=(
                    HashSignature(
                        rule_id="bad-hash",
                        sha256="not-a-digest",
                        classification="test-malware",
                    ),
                )
            )
        with self.assertRaisesRegex(ValueError, "invalid_byte_signature"):
            SignatureDatabase(
                byte_patterns=(
                    ByteSignature(
                        rule_id="empty-pattern",
                        pattern=b"",
                        classification="test-malware",
                    ),
                )
            )

    def test_regular_file_is_streamed_hashed_and_not_modified(self) -> None:
        target = self.root / "document.txt"
        content = b"ordinary local document"
        target.write_bytes(content)
        before = target.stat()
        self._start()

        result = self._scan("document.txt")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["verdict"], "no_threat_detected")
        self.assertEqual(result["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(result["size_bytes"], len(content))
        self.assertEqual(result["observations"], [])
        self.assertEqual(
            result["detectors"],
            [
                {
                    "detector": "local-signatures",
                    "version": "builtin-v1",
                    "state": "completed",
                    "reason_code": None,
                }
            ],
        )
        self.assertEqual(target.read_bytes(), content)
        self.assertEqual(target.stat().st_mtime_ns, before.st_mtime_ns)

    def test_hash_signature_detects_exact_known_sample(self) -> None:
        content = b"harmless test malware sample"
        (self.root / "sample.bin").write_bytes(content)
        signatures = SignatureDatabase(
            hashes=(
                HashSignature(
                    rule_id="test-hash",
                    sha256=hashlib.sha256(content).hexdigest(),
                    classification="test-malware",
                    severity="critical",
                ),
            )
        )
        self._start(signatures=signatures)

        result = self._scan("sample.bin")

        self.assertEqual(result["verdict"], "malware_detected")
        self.assertEqual(
            result["observations"],
            [
                {
                    "detector": "sha256-signature",
                    "rule_id": "test-hash",
                    "classification": "test-malware",
                    "severity": "critical",
                }
            ],
        )

    def test_byte_signature_matches_across_chunk_boundary(self) -> None:
        pattern = b"BOUNDARY-SIGNATURE"
        (self.root / "sample.bin").write_bytes(b"A" * 1020 + pattern + b"B")
        signatures = SignatureDatabase(
            byte_patterns=(
                ByteSignature(
                    rule_id="test-pattern",
                    pattern=pattern,
                    classification="test-malware",
                ),
            )
        )
        scanner = security.FileScanner(
            {"downloads": self.root}, signatures=signatures, chunk_bytes=1024
        )

        result = scanner.scan("downloads", "sample.bin").to_dict()

        self.assertEqual(result["verdict"], "malware_detected")
        self.assertEqual(result["observations"][0]["detector"], "byte-signature")

    def test_empty_file_is_scanned(self) -> None:
        (self.root / "empty").write_bytes(b"")
        self._start()

        result = self._scan("empty")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["size_bytes"], 0)
        self.assertEqual(result["sha256"], hashlib.sha256(b"").hexdigest())

    def test_absolute_traversal_directory_and_missing_resource_are_rejected(self) -> None:
        (self.root / "folder").mkdir()
        self._start()

        cases = (
            ("/etc/passwd", "invalid_relative_path"),
            ("../outside", "invalid_relative_path"),
            ("folder", "not_regular_file"),
            ("missing", "resource_not_accessible"),
        )
        for path, error in cases:
            with self.subTest(path=path):
                result = self._scan(path)
                self.assertEqual(result["verdict"], "unknown")
                self.assertEqual(result["error_code"], error)

        unavailable = security.FileScanner({}).scan("downloads", "file").to_dict()
        self.assertEqual(unavailable["error_code"], "resource_not_available")

    def test_oversized_file_is_rejected_without_digest(self) -> None:
        (self.root / "large.bin").write_bytes(b"x" * 1025)
        scanner = security.FileScanner({"downloads": self.root}, max_file_bytes=1024)

        result = scanner.scan("downloads", "large.bin").to_dict()

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["verdict"], "unknown")
        self.assertEqual(result["error_code"], "file_too_large")
        self.assertIsNone(result["sha256"])

    def test_symlink_is_rejected_when_supported(self) -> None:
        outside = self.root.parent / f"{self.root.name}-outside.txt"
        outside.write_bytes(b"outside")
        link = self.root / "link.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            outside.unlink(missing_ok=True)
            self.skipTest("symlinks are unavailable for this test account")
        try:
            self._start()
            result = self._scan("link.txt")
            self.assertEqual(result["verdict"], "unknown")
            self.assertEqual(result["error_code"], "resource_not_accessible")
        finally:
            outside.unlink(missing_ok=True)

    def test_timeout_is_unknown_not_clean(self) -> None:
        (self.root / "file.bin").write_bytes(b"content")
        scanner = security.FileScanner(
            {"downloads": self.root}, timeout_seconds=0.01
        )
        with mock.patch(
            "ai_native_security.scanner.time.monotonic", side_effect=[0.0, 1.0]
        ):
            result = scanner.scan("downloads", "file.bin").to_dict()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["verdict"], "unknown")
        self.assertEqual(result["error_code"], "scan_timeout")

    def test_file_change_during_scan_is_unknown_not_clean(self) -> None:
        target = self.root / "changing.bin"
        target.write_bytes(b"A" * 2048)
        scanner = security.FileScanner(
            {"downloads": self.root}, chunk_bytes=1024
        )
        original_read = os.read
        calls = 0

        def read_and_change(descriptor: int, size: int) -> bytes:
            nonlocal calls
            block = original_read(descriptor, size)
            calls += 1
            if calls == 1:
                target.write_bytes(b"B" * 4096)
            return block

        with mock.patch("ai_native_security.scanner.os.read", side_effect=read_and_change):
            result = scanner.scan("downloads", "changing.bin").to_dict()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["verdict"], "unknown")
        self.assertEqual(result["error_code"], "file_changed_during_scan")

    def test_payload_and_public_output_are_bounded(self) -> None:
        (self.root / "file.bin").write_bytes(b"content")
        self._start()
        with self.assertRaisesRegex(ValueError, "invalid_payload"):
            security.worker_invoke(
                "scan",
                {"resource_id": "downloads", "relative_path": "file.bin", "extra": 1},
            )
        result = self._scan("file.bin")
        encoded = json.dumps(result, sort_keys=True)
        self.assertLessEqual(len(encoded), 4096)
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn("content", encoded)

    def test_configured_detector_failure_is_partial_and_never_clean(self) -> None:
        (self.root / "file.bin").write_bytes(b"ordinary")
        self._start(external_detectors=(_FakeDetector(fail_begin=True),))

        result = self._scan("file.bin")

        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["verdict"], "unknown")
        self.assertEqual(result["error_code"], "detector_unavailable")
        self.assertEqual(result["detectors"][1]["state"], "unavailable")

    def test_local_detection_survives_external_detector_failure(self) -> None:
        content = b"known local signature"
        (self.root / "file.bin").write_bytes(content)
        signatures = SignatureDatabase(
            hashes=(
                HashSignature(
                    rule_id="known-local",
                    sha256=hashlib.sha256(content).hexdigest(),
                    classification="test-malware",
                ),
            )
        )
        self._start(
            signatures=signatures,
            external_detectors=(_FakeDetector(fail_finish=True),),
        )

        result = self._scan("file.bin")

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["verdict"], "malware_detected")
        self.assertEqual(result["error_code"], "detector_protocol_error")
        self.assertEqual(result["observations"][0]["rule_id"], "known-local")

    def test_external_detector_match_produces_completed_malware_verdict(self) -> None:
        (self.root / "file.bin").write_bytes(b"ordinary")
        self._start(external_detectors=(_FakeDetector(match=True),))

        result = self._scan("file.bin")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["verdict"], "malware_detected")
        self.assertIsNone(result["error_code"])
        self.assertEqual(result["observations"][0]["detector"], "clamd")
        self.assertEqual(result["detectors"][1]["state"], "completed")

    def test_detected_file_is_deduplicated_in_finding_store(self) -> None:
        content = b"known repeated signature"
        (self.root / "sample.bin").write_bytes(content)
        signatures = SignatureDatabase(
            hashes=(
                HashSignature(
                    rule_id="repeated-test",
                    sha256=hashlib.sha256(content).hexdigest(),
                    classification="test-malware",
                ),
            )
        )
        self._start(signatures=signatures)

        self._scan("sample.bin")
        self._scan("sample.bin")
        listed = security.worker_invoke("findings_list", {})

        self.assertEqual(listed["schema_version"], 1)
        self.assertEqual(len(listed["findings"]), 1)
        finding = listed["findings"][0]
        self.assertEqual(finding["relative_path"], "sample.bin")
        self.assertEqual(finding["detector_version"], "custom-v1")
        self.assertEqual(finding["occurrence_count"], 2)
        self.assertNotIn(str(self.root), json.dumps(listed))
        self.assertNotIn(content.decode(), json.dumps(listed))

    def test_clean_file_is_not_stored_as_a_finding(self) -> None:
        (self.root / "clean.txt").write_bytes(b"clean")
        self._start()

        self._scan("clean.txt")

        self.assertEqual(
            security.worker_invoke("findings_list", {})["findings"], []
        )

    def test_quick_and_full_profiles_scan_trusted_root(self) -> None:
        clean = b"ordinary"
        threat = b"campaign threat"
        (self.root / "a-clean.txt").write_bytes(clean)
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "b-threat.bin").write_bytes(threat)
        signatures = SignatureDatabase(
            hashes=(
                HashSignature(
                    rule_id="campaign-test",
                    sha256=hashlib.sha256(threat).hexdigest(),
                    classification="test-malware",
                ),
            )
        )
        self._start(signatures=signatures)

        quick = security.worker_invoke(
            "scan_profile", {"resource_id": "downloads", "mode": "quick"}
        )
        full = security.worker_invoke(
            "scan_profile", {"resource_id": "downloads", "mode": "full"}
        )

        self.assertEqual(quick["status"], "completed")
        self.assertEqual(quick["verdict"], "malware_detected")
        self.assertEqual(quick["scanned_files"], 2)
        self.assertEqual(quick["threat_files"], 1)
        self.assertEqual(full["verdict"], "malware_detected")
        self.assertTrue(full["finding_ids"])
        self.assertLessEqual(len(json.dumps(full)), 4096)

    def test_profile_can_scan_one_bounded_subdirectory(self) -> None:
        (self.root / "outside.txt").write_bytes(b"outside")
        selected = self.root / "Downloads"
        selected.mkdir()
        (selected / "inside.txt").write_bytes(b"inside")
        self._start()

        result = security.worker_invoke(
            "scan_profile",
            {
                "resource_id": "downloads",
                "relative_path": "Downloads",
                "mode": "full",
            },
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["relative_path"], "Downloads")
        self.assertEqual(result["scanned_files"], 1)

    def test_profile_rejects_escaping_or_missing_subdirectory(self) -> None:
        self._start()

        for relative_path in ("../outside", "/etc", "missing"):
            result = security.worker_invoke(
                "scan_profile",
                {
                    "resource_id": "downloads",
                    "relative_path": relative_path,
                    "mode": "full",
                },
            )
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["error_code"], "directory_not_available")

    def test_profile_limit_is_partial_not_clean(self) -> None:
        from ai_native_security import ScanProfile

        for name in ("a.txt", "b.txt"):
            (self.root / name).write_bytes(b"clean")
        profiles = {
            "quick": ScanProfile(1, 1024, 1, 5),
            "full": ScanProfile(1, 1024, 1, 5),
        }
        self._start(scan_profiles=profiles)

        result = security.worker_invoke(
            "scan_profile", {"resource_id": "downloads", "mode": "quick"}
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["verdict"], "unknown")
        self.assertEqual(result["error_code"], "profile_limit_reached")
        self.assertEqual(result["scanned_files"], 1)

    def test_profile_and_finding_payloads_are_closed_and_bounded(self) -> None:
        self._start()
        with self.assertRaisesRegex(ValueError, "invalid_payload"):
            security.worker_invoke(
                "scan_profile", {"resource_id": "downloads", "mode": "quick", "x": 1}
            )
        with self.assertRaisesRegex(ValueError, "invalid_finding_limit"):
            security.worker_invoke("findings_list", {"limit": 26})
        rejected = security.worker_invoke(
            "scan_profile", {"resource_id": "downloads", "mode": "unknown"}
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["error_code"], "invalid_scan_mode")

    def test_finding_database_cannot_be_placed_inside_scan_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "finding_database_inside_scan_root"):
            security.worker_start(
                {"downloads": self.root},
                finding_database=self.root / "private" / "findings.sqlite3",
            )

    def test_explicit_private_state_exclusion_is_never_scanned(self) -> None:
        private = self.root / "private"
        private.mkdir()
        security.worker_start(
            {"downloads": self.root},
            finding_database=private / "findings.sqlite3",
            excluded_paths={"downloads": ["private"]},
        )

        direct = self._scan("private/findings.sqlite3")
        campaign = security.worker_invoke(
            "scan_profile", {"resource_id": "downloads", "mode": "full"}
        )

        self.assertEqual(direct["status"], "rejected")
        self.assertEqual(direct["error_code"], "protected_path")
        self.assertEqual(campaign["status"], "completed")
        self.assertEqual(campaign["scanned_files"], 0)

    def test_scan_exclusions_are_closed_and_relative(self) -> None:
        invalid = (
            {"unknown": ["private"]},
            {"downloads": ["../private"]},
            {"downloads": ["/private"]},
        )
        for exclusions in invalid:
            with self.subTest(exclusions=exclusions):
                with self.assertRaisesRegex(ValueError, "invalid_scan_exclusions"):
                    security.worker_start(
                        {"downloads": self.root},
                        finding_database=self.findings_database,
                        excluded_paths=exclusions,
                    )

    def test_symlink_makes_profile_partial_when_supported(self) -> None:
        outside = self.root.parent / f"{self.root.name}-campaign-outside.txt"
        outside.write_bytes(b"outside")
        link = self.root / "outside-link.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            outside.unlink(missing_ok=True)
            self.skipTest("symlinks are unavailable for this test account")
        try:
            self._start()
            result = security.worker_invoke(
                "scan_profile", {"resource_id": "downloads", "mode": "quick"}
            )
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["verdict"], "unknown")
            self.assertEqual(result["error_code"], "scan_incomplete")
            self.assertEqual(result["skipped_files"], 1)
        finally:
            outside.unlink(missing_ok=True)

    def test_tampered_finding_record_is_not_returned(self) -> None:
        content = b"tamper-safe finding"
        (self.root / "sample.bin").write_bytes(content)
        signatures = SignatureDatabase(
            hashes=(
                HashSignature(
                    rule_id="tamper-test",
                    sha256=hashlib.sha256(content).hexdigest(),
                    classification="test-malware",
                ),
            )
        )
        self._start(signatures=signatures)
        self._scan("sample.bin")
        security.worker_stop()
        connection = sqlite3.connect(self.findings_database)
        try:
            connection.execute(
                "UPDATE security_findings SET relative_path = ?",
                ("C:/private/secret.bin",),
            )
            connection.commit()
        finally:
            connection.close()
        store = security.FindingStore(self.findings_database)
        try:
            with self.assertRaisesRegex(
                security.FindingStoreError, "invalid_finding_record"
            ):
                store.list()
        finally:
            store.close()

    def test_maximum_finding_page_fits_worker_response_limit(self) -> None:
        store = security.FindingStore(":memory:")
        observation = DetectorObservation(
            detector="sha256-signature",
            rule_id="bounded-page",
            classification="test-malware",
            severity="high",
        )
        detector = security.DetectorStatus(
            detector="local-signatures",
            version="test-v1",
            state="completed",
        )
        try:
            for index in range(25):
                store.record(
                    security.ScanResult(
                        resource_id="downloads",
                        relative_path="a" * 990 + f"-{index:02d}",
                        status="completed",
                        verdict="malware_detected",
                        sha256=f"{index:064x}",
                        size_bytes=index,
                        observations=(observation,),
                        detectors=(detector,),
                    )
                )
            output = store.list(limit=25)
        finally:
            store.close()

        self.assertEqual(len(output["findings"]), 25)
        self.assertLessEqual(len(json.dumps(output).encode("utf-8")), 64 * 1024)

    def test_quarantine_commit_and_restore_are_reversible_and_one_time(self) -> None:
        content = b"reversible quarantine sample"
        target = self.root / "sample.bin"
        target.write_bytes(content)
        signatures = SignatureDatabase(
            hashes=(
                HashSignature(
                    rule_id="quarantine-test",
                    sha256=hashlib.sha256(content).hexdigest(),
                    classification="test-malware",
                ),
            )
        )
        self._start(signatures=signatures)
        self._scan("sample.bin")
        finding_id = security.worker_invoke("findings_list", {})["findings"][0]["finding_id"]

        prepared = security.worker_invoke("quarantine_prepare", {"finding_id": finding_id})
        self.assertEqual(prepared["state"], "awaiting_confirmation")
        self.assertEqual(target.read_bytes(), content)

        quarantined = security.worker_invoke(
            "quarantine_commit", {"quarantine_id": prepared["quarantine_id"]}
        )
        self.assertEqual(quarantined["state"], "quarantined")
        self.assertFalse(target.exists())
        objects = list((self.quarantine_root / "objects").iterdir())
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0].read_bytes(), content)
        with self.assertRaisesRegex(ValueError, "invalid_quarantine_state"):
            security.worker_invoke(
                "quarantine_commit", {"quarantine_id": prepared["quarantine_id"]}
            )

        security.worker_stop()
        self._start(signatures=signatures)

        restored = security.worker_invoke(
            "quarantine_restore", {"quarantine_id": prepared["quarantine_id"]}
        )
        self.assertEqual(restored["state"], "restored")
        self.assertEqual(target.read_bytes(), content)
        self.assertEqual(security.worker_invoke("findings_list", {})["findings"][0]["state"], "active")

    def test_quarantine_rejects_file_changed_after_prepare(self) -> None:
        original = b"original threat"
        target = self.root / "changing.bin"
        target.write_bytes(original)
        signatures = SignatureDatabase(
            hashes=(
                HashSignature(
                    rule_id="changing-test",
                    sha256=hashlib.sha256(original).hexdigest(),
                    classification="test-malware",
                ),
            )
        )
        self._start(signatures=signatures)
        self._scan("changing.bin")
        finding_id = security.worker_invoke("findings_list", {})["findings"][0]["finding_id"]
        prepared = security.worker_invoke("quarantine_prepare", {"finding_id": finding_id})
        target.write_bytes(b"changed after approval preview")

        result = security.worker_invoke(
            "quarantine_commit", {"quarantine_id": prepared["quarantine_id"]}
        )

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error_code"], "finding_asset_changed")
        self.assertEqual(target.read_bytes(), b"changed after approval preview")

    def test_restore_never_overwrites_existing_destination(self) -> None:
        content = b"restore collision threat"
        target = self.root / "collision.bin"
        target.write_bytes(content)
        signatures = SignatureDatabase(
            hashes=(
                HashSignature(
                    rule_id="collision-test",
                    sha256=hashlib.sha256(content).hexdigest(),
                    classification="test-malware",
                ),
            )
        )
        self._start(signatures=signatures)
        self._scan("collision.bin")
        finding_id = security.worker_invoke("findings_list", {})["findings"][0]["finding_id"]
        prepared = security.worker_invoke("quarantine_prepare", {"finding_id": finding_id})
        security.worker_invoke(
            "quarantine_commit", {"quarantine_id": prepared["quarantine_id"]}
        )
        target.write_bytes(b"new unrelated file")

        result = security.worker_invoke(
            "quarantine_restore", {"quarantine_id": prepared["quarantine_id"]}
        )

        self.assertEqual(result["state"], "quarantined")
        self.assertEqual(result["error_code"], "restore_destination_exists")
        self.assertEqual(target.read_bytes(), b"new unrelated file")


class _FakeDetector:
    name = "clamd"
    version = "fake-v1"

    def __init__(
        self,
        *,
        fail_begin: bool = False,
        fail_finish: bool = False,
        match: bool = False,
    ):
        self.fail_begin = fail_begin
        self.fail_finish = fail_finish
        self.match = match

    def begin(self, _deadline: float) -> "_FakeSession":
        if self.fail_begin:
            raise DetectorError("detector_unavailable", unavailable=True)
        return _FakeSession(fail_finish=self.fail_finish, match=self.match)


class _FakeSession:
    def __init__(self, *, fail_finish: bool, match: bool):
        self.fail_finish = fail_finish
        self.match = match

    def feed(self, _block: bytes) -> None:
        return None

    def finish(self) -> tuple[DetectorObservation, ...]:
        if self.fail_finish:
            raise DetectorError("detector_protocol_error")
        if not self.match:
            return ()
        return (
            DetectorObservation(
                detector="clamd",
                rule_id="Fake-Test-Signature",
                classification="malware",
                severity="high",
            ),
        )

    def abort(self) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
