"""Opt-in smoke against an installed clamd daemon on Ubuntu."""

from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

import ai_native_security as security


pytestmark = pytest.mark.skipif(
    os.environ.get("AI_NATIVE_RUN_REAL_CLAMD") != "1",
    reason="requires the explicit real-clamd smoke profile",
)


def test_real_clamd_scans_bounded_stream_with_verified_peer(tmp_path: Path) -> None:
    socket_path = Path(os.environ["AI_NATIVE_REAL_CLAMD_SOCKET"])
    expected_uid = int(os.environ["AI_NATIVE_REAL_CLAMD_UID"])
    socket_info = socket_path.lstat()
    assert socket_path.is_absolute()
    assert not socket_path.is_symlink()
    assert stat.S_ISSOCK(socket_info.st_mode)

    scan_root = tmp_path / "scan"
    scan_root.mkdir()
    (scan_root / "clean.txt").write_bytes(b"ordinary clean document")
    (scan_root / "synthetic.txt").write_bytes(
        b"prefix AI_NATIVE_SECURITY_REAL_CLAMD_SYNTHETIC_V1 suffix"
    )
    detector = security.ClamdUnixSocketDetector(
        socket_path=socket_path,
        expected_peer_uids=frozenset({expected_uid}),
        timeout_seconds=10,
    )
    scanner = security.FileScanner(
        {"fixture": scan_root},
        external_detectors=(detector,),
        timeout_seconds=15,
    )

    clean = scanner.scan("fixture", "clean.txt")
    detected = scanner.scan("fixture", "synthetic.txt")

    assert clean.status == "completed"
    assert clean.verdict == "no_threat_detected"
    assert [item.state for item in clean.detectors] == ["completed", "completed"]
    assert detected.status == "completed"
    assert detected.verdict == "malware_detected"
    assert len(detected.observations) == 1
    observation = detected.observations[0]
    assert observation.detector == "clamd"
    assert observation.classification == "malware"
    assert observation.rule_id.startswith("AiNative-Test-Synthetic")
