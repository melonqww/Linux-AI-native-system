"""Linux-only integration checks for Security Center trust boundaries."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import socket
import stat
import sys
import threading
import time

import pytest

import ai_native_security as security
from ai_native_security.detectors import DetectorError


pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="requires Linux AF_UNIX peer credentials and filesystem modes",
)


class _UnixClamdStub:
    """One-connection INSTREAM peer; it never executes or inspects a file."""

    def __init__(self, socket_path: Path, reply: bytes) -> None:
        self.socket_path = socket_path
        self.reply = reply
        self.command = b""
        self.payload = b""
        self.error: BaseException | None = None
        self._ready = threading.Event()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "_UnixClamdStub":
        self._thread.start()
        assert self._ready.wait(2), "Unix detector stub did not start"
        return self

    def __exit__(self, *_exc: object) -> None:
        assert self._done.wait(2), "Unix detector stub did not stop"
        self._thread.join(timeout=1)
        if self.error is not None:
            raise self.error

    def _serve(self) -> None:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(os.fspath(self.socket_path))
                listener.listen(1)
                self._ready.set()
                connection, _address = listener.accept()
                with connection:
                    self.command = _receive_exact(connection, len(b"zINSTREAM\0"))
                    if not self.command:
                        return
                    chunks: list[bytes] = []
                    while True:
                        header = _receive_exact(connection, 4)
                        if len(header) != 4:
                            raise AssertionError("truncated INSTREAM header")
                        size = int.from_bytes(header, "big")
                        if size == 0:
                            break
                        chunks.append(_receive_exact(connection, size))
                    self.payload = b"".join(chunks)
                    connection.sendall(self.reply)
        except BaseException as error:
            self.error = error
            self._ready.set()
        finally:
            self._done.set()


def _receive_exact(connection: socket.socket, size: int) -> bytes:
    output = bytearray()
    while len(output) < size:
        block = connection.recv(size - len(output))
        if not block:
            break
        output.extend(block)
    return bytes(output)


def test_real_unix_peer_credentials_and_instream_framing(tmp_path: Path) -> None:
    socket_path = tmp_path / "clamd.sock"
    content = b"safe synthetic Linux integration fixture"
    with _UnixClamdStub(
        socket_path, b"stream: Campaign-Synthetic FOUND\0"
    ) as peer:
        detector = security.ClamdUnixSocketDetector(
            socket_path=socket_path,
            expected_peer_uids=frozenset({os.getuid()}),
        )
        session = detector.begin(time.monotonic() + 5)
        session.feed(content[:12])
        session.feed(content[12:])
        observations = session.finish()

    assert peer.command == b"zINSTREAM\0"
    assert peer.payload == content
    assert len(observations) == 1
    assert observations[0].rule_id == "Campaign-Synthetic"


def test_real_unix_peer_uid_rejection_sends_no_command(tmp_path: Path) -> None:
    socket_path = tmp_path / "clamd.sock"
    with _UnixClamdStub(socket_path, b"stream: OK\0") as peer:
        detector = security.ClamdUnixSocketDetector(
            socket_path=socket_path,
            expected_peer_uids=frozenset({os.getuid() + 1}),
        )
        with pytest.raises(DetectorError, match="detector_identity_rejected"):
            detector.begin(time.monotonic() + 5)

    assert peer.command == b""
    assert peer.payload == b""


def test_linux_symlink_cannot_escape_scan_root(tmp_path: Path) -> None:
    scan_root = tmp_path / "scan"
    scan_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside canary")
    (scan_root / "escape.txt").symlink_to(outside)

    result = security.FileScanner({"fixture": scan_root}).scan(
        "fixture", "escape.txt"
    )

    assert result.status == "rejected"
    assert result.verdict == "unknown"
    assert result.error_code == "resource_not_accessible"
    assert outside.read_bytes() == b"outside canary"


def test_linux_quarantine_storage_and_receipts_are_private(tmp_path: Path) -> None:
    scan_root = tmp_path / "scan"
    scan_root.mkdir()
    content = b"safe synthetic quarantine fixture"
    target = scan_root / "fixture.bin"
    target.write_bytes(content)
    private = tmp_path / "private"
    findings = tmp_path / "state" / "findings.sqlite3"
    receipts = tmp_path / "state" / "quarantine.sqlite3"
    signatures = security.SignatureDatabase(
        hashes=(
            security.HashSignature(
                rule_id="linux-integration",
                sha256=hashlib.sha256(content).hexdigest(),
                classification="synthetic-test-marker",
            ),
        ),
        version="linux-integration-v1",
    )

    security.worker_start(
        {"fixture": scan_root},
        signatures=signatures,
        finding_database=findings,
        quarantine_root=private,
        quarantine_database=receipts,
    )
    try:
        scanned = security.worker_invoke(
            "scan", {"resource_id": "fixture", "relative_path": "fixture.bin"}
        )
        assert scanned["verdict"] == "malware_detected"
        finding_id = security.worker_invoke("findings_list", {})["findings"][0][
            "finding_id"
        ]
        prepared = security.worker_invoke(
            "quarantine_prepare", {"finding_id": finding_id}
        )
        committed = security.worker_invoke(
            "quarantine_commit", {"quarantine_id": prepared["quarantine_id"]}
        )
        assert committed["state"] == "quarantined"

        objects = tuple((private / "objects").iterdir())
        assert len(objects) == 1
        assert stat.S_IMODE(private.stat().st_mode) == 0o700
        assert stat.S_IMODE((private / "objects").stat().st_mode) == 0o700
        assert stat.S_IMODE(objects[0].stat().st_mode) == 0o600
        assert stat.S_IMODE(findings.stat().st_mode) == 0o600
        assert stat.S_IMODE(receipts.stat().st_mode) == 0o600
    finally:
        security.worker_stop()
