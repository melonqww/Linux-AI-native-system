"""Optional streaming detector adapters with bounded local IPC."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import socket
import stat
import struct
import time
from typing import Protocol

from .contracts import DetectorObservation


_SAFE_SIGNATURE = re.compile(rb"^[A-Za-z0-9_.-]{1,48}$")


class DetectorError(RuntimeError):
    """A normalized detector failure safe to expose as a reason code."""

    def __init__(self, reason_code: str, *, unavailable: bool = False) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.unavailable = unavailable


class StreamDetectorSession(Protocol):
    def feed(self, block: bytes) -> None: ...

    def finish(self) -> tuple[DetectorObservation, ...]: ...

    def abort(self) -> None: ...


class StreamDetector(Protocol):
    name: str
    version: str

    def begin(self, deadline: float) -> StreamDetectorSession: ...


@dataclass(frozen=True, slots=True)
class ClamdUnixSocketDetector:
    """Submit bytes to a peer-authenticated local clamd INSTREAM endpoint."""

    socket_path: Path
    expected_peer_uids: frozenset[int]
    timeout_seconds: float = 5.0
    max_reply_bytes: int = 4096
    name: str = field(init=False, default="clamd")
    version: str = field(init=False, default="instream-v1")

    def __post_init__(self) -> None:
        if not isinstance(self.socket_path, Path):
            raise TypeError("clamd_socket_path_must_be_path")
        raw_path = os.fspath(self.socket_path)
        if (
            not self.socket_path.is_absolute()
            or not raw_path
            or "\x00" in raw_path
            or len(os.fsencode(raw_path)) > 256
        ):
            raise ValueError("invalid_clamd_socket_path")
        if (
            type(self.expected_peer_uids) is not frozenset
            or not self.expected_peer_uids
            or len(self.expected_peer_uids) > 16
            or any(
                isinstance(uid, bool) or not isinstance(uid, int) or not 0 <= uid < 2**31
                for uid in self.expected_peer_uids
            )
        ):
            raise ValueError("invalid_clamd_peer_uids")
        if not 0.01 <= self.timeout_seconds <= 30:
            raise ValueError("invalid_clamd_timeout")
        if not 128 <= self.max_reply_bytes <= 16 * 1024:
            raise ValueError("invalid_clamd_reply_limit")

    def begin(self, deadline: float) -> StreamDetectorSession:
        if not hasattr(socket, "AF_UNIX") or not hasattr(socket, "SO_PEERCRED"):
            raise DetectorError("detector_unavailable", unavailable=True)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DetectorError("detector_timeout")
        try:
            metadata = self.socket_path.lstat()
            if self.socket_path.is_symlink() or not stat.S_ISSOCK(metadata.st_mode):
                raise DetectorError("detector_identity_rejected")
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(min(self.timeout_seconds, remaining))
            connection.connect(os.fspath(self.socket_path))
            credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            if len(credentials) != 12:
                raise DetectorError("detector_identity_rejected")
            _pid, uid, _gid = struct.unpack("3i", credentials)
            if uid not in self.expected_peer_uids:
                raise DetectorError("detector_identity_rejected")
            connection.sendall(b"zINSTREAM\x00")
            return _ClamdSession(
                connection,
                self.max_reply_bytes,
                deadline,
                self.timeout_seconds,
            )
        except DetectorError:
            if "connection" in locals():
                connection.close()
            raise
        except (FileNotFoundError, ConnectionRefusedError) as error:
            if "connection" in locals():
                connection.close()
            raise DetectorError("detector_unavailable", unavailable=True) from error
        except (TimeoutError, socket.timeout) as error:
            if "connection" in locals():
                connection.close()
            raise DetectorError("detector_timeout") from error
        except OSError as error:
            if "connection" in locals():
                connection.close()
            raise DetectorError("detector_unavailable", unavailable=True) from error


class _ClamdSession:
    def __init__(
        self,
        connection: socket.socket,
        max_reply_bytes: int,
        deadline: float,
        operation_timeout: float,
    ) -> None:
        self._connection = connection
        self._max_reply_bytes = max_reply_bytes
        self._deadline = deadline
        self._operation_timeout = operation_timeout
        self._closed = False

    def feed(self, block: bytes) -> None:
        if self._closed or not block or len(block) > 1024 * 1024:
            raise DetectorError("detector_protocol_error")
        try:
            self._set_remaining_timeout()
            self._connection.sendall(struct.pack(">I", len(block)))
            self._set_remaining_timeout()
            self._connection.sendall(block)
        except (TimeoutError, socket.timeout) as error:
            raise DetectorError("detector_timeout") from error
        except OSError as error:
            raise DetectorError("detector_protocol_error") from error

    def finish(self) -> tuple[DetectorObservation, ...]:
        if self._closed:
            raise DetectorError("detector_protocol_error")
        try:
            self._set_remaining_timeout()
            self._connection.sendall(struct.pack(">I", 0))
            reply = self._read_reply()
            return _parse_clamd_reply(reply)
        finally:
            self.abort()

    def abort(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._connection.close()
            except OSError:
                pass

    def _read_reply(self) -> bytes:
        reply = bytearray()
        try:
            while len(reply) <= self._max_reply_bytes:
                self._set_remaining_timeout()
                block = self._connection.recv(
                    min(1024, self._max_reply_bytes + 1 - len(reply))
                )
                if not block:
                    break
                reply.extend(block)
                terminator = reply.find(0)
                if terminator >= 0:
                    if terminator != len(reply) - 1:
                        raise DetectorError("detector_protocol_error")
                    return bytes(reply[:terminator])
        except (TimeoutError, socket.timeout) as error:
            raise DetectorError("detector_timeout") from error
        except OSError as error:
            raise DetectorError("detector_protocol_error") from error
        raise DetectorError("detector_protocol_error")

    def _set_remaining_timeout(self) -> None:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise DetectorError("detector_timeout")
        self._connection.settimeout(min(self._operation_timeout, remaining))


def _parse_clamd_reply(reply: bytes) -> tuple[DetectorObservation, ...]:
    if reply == b"stream: OK":
        return ()
    prefix = b"stream: "
    suffix = b" FOUND"
    if not reply.startswith(prefix) or not reply.endswith(suffix):
        raise DetectorError("detector_protocol_error")
    raw_signature = reply[len(prefix) : -len(suffix)]
    if not raw_signature:
        raise DetectorError("detector_protocol_error")
    if _SAFE_SIGNATURE.fullmatch(raw_signature):
        rule_id = raw_signature.decode("ascii")
    else:
        rule_id = "clamd-" + hashlib.sha256(raw_signature).hexdigest()[:24]
    return (
        DetectorObservation(
            detector="clamd",
            rule_id=rule_id,
            classification="malware",
            severity="high",
        ),
    )


__all__ = [
    "ClamdUnixSocketDetector",
    "DetectorError",
    "StreamDetector",
    "StreamDetectorSession",
]
