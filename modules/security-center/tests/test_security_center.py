import importlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import unittest
import uuid
from dataclasses import FrozenInstanceError
from unittest import mock

import ai_native_security as security
from ai_native_security import SecurityModuleStatus
from ai_native_security import ByteSignature, HashSignature, SignatureDatabase


EXPECTED_STATUS = {
    "schema_version": 1,
    "module_id": "security.center",
    "module_version": "0.2.0",
    "state": "ready",
    "lifecycle": "on-demand",
    "capabilities": ["security.module.status", "security.files.scan"],
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

    def tearDown(self) -> None:
        security.worker_stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def _start(self, *, signatures: SignatureDatabase | None = None) -> None:
        security.worker_start({"downloads": self.root}, signatures=signatures)

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


if __name__ == "__main__":
    unittest.main()
