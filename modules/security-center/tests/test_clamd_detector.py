import os
from pathlib import Path
import socket
import stat
import struct
import time
import unittest
from unittest import mock

from ai_native_security import ClamdUnixSocketDetector
from ai_native_security.detectors import DetectorError


class _FakeSocket:
    def __init__(self, reply: bytes, *, uid: int = 123) -> None:
        self.reply = [reply]
        self.uid = uid
        self.sent: list[bytes] = []
        self.timeout: float | None = None
        self.connected_to: str | None = None
        self.closed = False

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def connect(self, value: str) -> None:
        self.connected_to = value

    def getsockopt(self, _level: int, _option: int, _length: int) -> bytes:
        return struct.pack("3i", 10, self.uid, 20)

    def sendall(self, value: bytes) -> None:
        self.sent.append(value)

    def recv(self, _size: int) -> bytes:
        return self.reply.pop(0) if self.reply else b""

    def close(self) -> None:
        self.closed = True


class ClamdUnixSocketDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path("C:/run/clamd.sock")

    def _begin(self, fake: _FakeSocket):
        detector = ClamdUnixSocketDetector(
            socket_path=self.path,
            expected_peer_uids=frozenset({123}),
        )
        socket_metadata = os.stat_result((stat.S_IFSOCK, 0, 0, 0, 0, 0, 0, 0, 0, 0))
        return detector, fake, mock.patch.multiple(
            "ai_native_security.detectors.socket",
            AF_UNIX=1,
            SO_PEERCRED=17,
            socket=mock.Mock(return_value=fake),
            create=True,
        ), mock.patch.object(type(self.path), "lstat", return_value=socket_metadata), mock.patch.object(
            type(self.path), "is_symlink", return_value=False
        )

    def test_instream_clean_reply_uses_bounded_binary_framing(self) -> None:
        detector, fake, socket_patch, lstat_patch, link_patch = self._begin(
            _FakeSocket(b"stream: OK\x00")
        )
        with socket_patch, lstat_patch, link_patch:
            session = detector.begin(time.monotonic() + 5)
            session.feed(b"sample")
            result = session.finish()

        self.assertEqual(result, ())
        self.assertEqual(fake.sent[0], b"zINSTREAM\x00")
        self.assertEqual(fake.sent[1], struct.pack(">I", 6))
        self.assertEqual(fake.sent[2], b"sample")
        self.assertEqual(fake.sent[3], struct.pack(">I", 0))
        self.assertTrue(fake.closed)

    def test_found_reply_is_normalized_without_returning_raw_protocol(self) -> None:
        detector, fake, socket_patch, lstat_patch, link_patch = self._begin(
            _FakeSocket(b"stream: Eicar-Test-Signature FOUND\x00")
        )
        with socket_patch, lstat_patch, link_patch:
            session = detector.begin(time.monotonic() + 5)
            session.feed(b"sample")
            result = session.finish()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].detector, "clamd")
        self.assertEqual(result[0].rule_id, "Eicar-Test-Signature")
        self.assertEqual(result[0].classification, "malware")

    def test_untrusted_signature_text_is_replaced_with_stable_digest(self) -> None:
        detector, _fake, socket_patch, lstat_patch, link_patch = self._begin(
            _FakeSocket(b"stream: unsafe name with spaces FOUND\x00")
        )
        with socket_patch, lstat_patch, link_patch:
            session = detector.begin(time.monotonic() + 5)
            session.feed(b"sample")
            result = session.finish()

        self.assertRegex(result[0].rule_id, r"^clamd-[0-9a-f]{24}$")
        self.assertNotIn("unsafe name", result[0].rule_id)

    def test_wrong_peer_uid_is_rejected_before_stream_command(self) -> None:
        detector, fake, socket_patch, lstat_patch, link_patch = self._begin(
            _FakeSocket(b"stream: OK\x00", uid=999)
        )
        with socket_patch, lstat_patch, link_patch:
            with self.assertRaisesRegex(DetectorError, "detector_identity_rejected"):
                detector.begin(time.monotonic() + 5)

        self.assertEqual(fake.sent, [])
        self.assertTrue(fake.closed)

    def test_malformed_or_oversized_reply_fails_closed(self) -> None:
        for reply in (b"unexpected\x00", b"x" * 129):
            with self.subTest(reply_length=len(reply)):
                fake = _FakeSocket(reply)
                detector = ClamdUnixSocketDetector(
                    socket_path=self.path,
                    expected_peer_uids=frozenset({123}),
                    max_reply_bytes=128,
                )
                socket_metadata = os.stat_result(
                    (stat.S_IFSOCK, 0, 0, 0, 0, 0, 0, 0, 0, 0)
                )
                with mock.patch.multiple(
                    "ai_native_security.detectors.socket",
                    AF_UNIX=1,
                    SO_PEERCRED=17,
                    socket=mock.Mock(return_value=fake),
                    create=True,
                ), mock.patch.object(
                    type(self.path), "lstat", return_value=socket_metadata
                ), mock.patch.object(type(self.path), "is_symlink", return_value=False):
                    session = detector.begin(time.monotonic() + 5)
                    session.feed(b"sample")
                    with self.assertRaisesRegex(
                        DetectorError, "detector_protocol_error"
                    ):
                        session.finish()

    def test_configuration_requires_absolute_path_and_peer_allowlist(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_clamd_socket_path"):
            ClamdUnixSocketDetector(Path("relative.sock"), frozenset({123}))
        with self.assertRaisesRegex(ValueError, "invalid_clamd_peer_uids"):
            ClamdUnixSocketDetector(self.path, frozenset())


if __name__ == "__main__":
    unittest.main()
