"""Linux Unix-domain runtime transport authenticated with SO_PEERCRED."""

from __future__ import annotations

import errno
import json
import os
import socket
import socketserver
import stat
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from .routing import RuntimeApplication, RuntimeRouter


MAX_MESSAGE_BYTES = 64 * 1024
SOCKET_MODE = 0o600
DIRECTORY_MODE = 0o700


@dataclass(frozen=True)
class PeerCredentials:
    pid: int
    uid: int
    gid: int


class PeerCredentialPolicy:
    """Accept only processes running under the runtime owner's real UID."""

    def __init__(
        self, *, expected_uid: int | None = None, expected_gid: int | None = None
    ) -> None:
        if not hasattr(os, "getuid"):
            raise RuntimeError("peer credentials require a Unix UID")
        self.expected_uid = os.getuid() if expected_uid is None else expected_uid
        self.expected_gid = os.getgid() if expected_gid is None else expected_gid

    def authorize(self, peer: PeerCredentials) -> bool:
        return (
            peer.pid > 0
            and peer.uid == self.expected_uid
            and peer.gid == self.expected_gid
        )


class _ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    # Keep the module importable for cross-platform contract tests. Creation is
    # still rejected below unless the host is Linux with AF_UNIX/SO_PEERCRED.
    address_family = getattr(socket, "AF_UNIX", socket.AF_INET)
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, path: Path, handler, router, policy) -> None:
        self.socket_path = path
        self.router = router
        self.peer_policy = policy
        self._socket_identity: tuple[int, int] | None = None
        super().__init__(str(path), handler, bind_and_activate=True)
        try:
            bound = path.lstat()
            if not stat.S_ISSOCK(bound.st_mode):
                raise RuntimeError("bound IPC path is not a socket")
            self._socket_identity = (bound.st_dev, bound.st_ino)
            os.chmod(path, SOCKET_MODE, follow_symlinks=False)
            info = path.lstat()
            if self._socket_identity != (info.st_dev, info.st_ino):
                raise RuntimeError("Unix socket path changed during setup")
        except Exception:
            super().server_close()
            try:
                current = path.lstat()
                if (
                    stat.S_ISSOCK(current.st_mode)
                    and self._socket_identity == (current.st_dev, current.st_ino)
                ):
                    path.unlink()
            except FileNotFoundError:
                pass
            raise

    def server_close(self) -> None:
        super().server_close()
        try:
            info = self.socket_path.lstat()
            if self._socket_identity == (info.st_dev, info.st_ino) and stat.S_ISSOCK(info.st_mode):
                self.socket_path.unlink()
        except FileNotFoundError:
            pass


class _UnixHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        request_id = str(uuid4())
        try:
            self.connection.settimeout(10)
            peer = self._peer_credentials()
            if not self.server.peer_policy.authorize(peer):
                self._send(403, self.server.router.error(403, "peer_not_authorized", False, request_id).payload, request_id)
                return
            raw = self.rfile.readline(MAX_MESSAGE_BYTES + 1)
            if not raw or len(raw) > MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
                raise ValueError("invalid_message_frame")
            request = json.loads(raw)
            method, path, body, request_id = self._validate_request(request)
            response = self.server.router.dispatch(
                method, path, body if method == "POST" else None, request_id=request_id
            )
            self._send(response.status, response.payload, request_id)
        except (ValueError, UnicodeError, json.JSONDecodeError):
            response = self.server.router.error(400, "invalid_request", False, request_id)
            self._send(response.status, response.payload, request_id)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, socket.timeout):
            return
        except Exception:
            response = self.server.router.error(500, "internal_error", True, request_id)
            self._send(response.status, response.payload, request_id)

    def _peer_credentials(self) -> PeerCredentials:
        option = getattr(socket, "SO_PEERCRED", None)
        if option is None:
            raise RuntimeError("SO_PEERCRED is unavailable")
        raw = self.connection.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
        return PeerCredentials(*struct.unpack("3i", raw))

    @staticmethod
    def _validate_request(value: object) -> tuple[str, str, dict[str, object], str]:
        if not isinstance(value, dict) or set(value) != {
            "version", "request_id", "method", "path", "body"
        }:
            raise ValueError("request fields do not match IPC contract")
        if value["version"] != 1:
            raise ValueError("unsupported IPC version")
        request_id = value["request_id"]
        if not isinstance(request_id, str) or str(UUID(request_id)) != request_id:
            raise ValueError("request_id must be a canonical UUID")
        method = value["method"]
        path = value["path"]
        body = value["body"]
        if method not in {"GET", "POST"}:
            raise ValueError("unsupported method")
        if not isinstance(path, str) or not path.startswith("/v1/") or len(path) > 256:
            raise ValueError("invalid path")
        if not isinstance(body, dict) or (method == "GET" and body):
            raise ValueError("body must be an object and empty for GET")
        return method, path, body, request_id

    def _send(self, status: int, body: object, request_id: str) -> None:
        payload = {
            "version": 1,
            "request_id": request_id,
            "status": status,
            "body": body,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_MESSAGE_BYTES:
            fallback = self.server.router.error(500, "response_too_large", False, request_id)
            payload = {
                "version": 1,
                "request_id": request_id,
                "status": fallback.status,
                "body": fallback.payload,
            }
            encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        try:
            self.wfile.write(encoded + b"\n")
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            return


def default_socket_path() -> Path:
    if not sys.platform.startswith("linux") or not hasattr(os, "getuid"):
        raise RuntimeError("Unix peer-credential transport requires Linux")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    root = Path(runtime) if runtime else Path("/run/user") / str(os.getuid())
    if not root.is_absolute():
        raise RuntimeError("XDG_RUNTIME_DIR must be absolute")
    return root / "ai-native-linux" / "runtime.sock"


def create_unix_server(
    application: RuntimeApplication,
    path: Path | None = None,
    *,
    peer_policy: PeerCredentialPolicy | None = None,
):
    if not sys.platform.startswith("linux"):
        raise RuntimeError("SO_PEERCRED Unix transport is supported only on Linux")
    if not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("Linux SO_PEERCRED is unavailable")
    socket_path = (path or default_socket_path()).absolute()
    if len(os.fsencode(socket_path)) > 100:
        raise ValueError("Unix socket path is too long")
    _prepare_parent(socket_path.parent)
    _remove_stale_socket(socket_path, os.getuid())
    return _ThreadingUnixServer(
        socket_path,
        _UnixHandler,
        RuntimeRouter(application),
        peer_policy or PeerCredentialPolicy(),
    )


def _prepare_parent(parent: Path) -> None:
    if not parent.exists():
        parent.mkdir(mode=DIRECTORY_MODE, parents=False)
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("Unix socket parent must be a normal directory")
    if info.st_uid != os.getuid():
        raise PermissionError("Unix socket parent must be owned by the runtime UID")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise PermissionError("Unix socket parent must not be accessible by group or others")


def _remove_stale_socket(path: Path, expected_uid: int) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != expected_uid:
        raise FileExistsError("refusing to replace non-owned or non-socket IPC path")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.2)
        probe.connect(str(path))
    except OSError as error:
        if error.errno not in {errno.ECONNREFUSED, errno.ENOENT}:
            raise
    else:
        raise OSError(errno.EADDRINUSE, "Unix runtime socket is already active")
    finally:
        probe.close()
    current = path.lstat()
    if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
        raise RuntimeError("Unix socket path changed during stale cleanup")
    path.unlink()
