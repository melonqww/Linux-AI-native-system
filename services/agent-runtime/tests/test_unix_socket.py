import json
import os
import shutil
import socket
import stat
import sys
import tempfile
import threading
import unittest
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from ai_native_linux.unix_socket import (
    PeerCredentialPolicy,
    PeerCredentials,
    create_unix_server,
)
from ai_native_permissions import TransportKind


@dataclass
class Result:
    path: str


class App:
    def __init__(self):
        self.transport_contexts = []

    def capabilities(self):
        return ["documents.query.search"]

    def search(self, payload):
        return [Result(payload["text"])]

    def index_status(self):
        return {"state": "idle"}

    def system_status(self):
        return {"schema_version": 1, "supported": True}

    def compile_intent(self, payload):
        return Result(payload["text"])

    def execute_plan(self, payload, *, transport_context):
        self.transport_contexts.append(transport_context)
        return Result(payload["plan_id"])

    def respond_to_approval(self, payload, *, transport_context):
        return Result(payload["approval_request_id"])


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux SO_PEERCRED integration")
class UnixSocketTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ai-native-ipc-"))
        self.root.chmod(0o700)
        self.path = self.root / "runtime.sock"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _request(self, server, *, method="GET", path="/v1/health", body=None):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        request_id = str(uuid4())
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect(str(self.path))
            request = {
                "version": 1,
                "request_id": request_id,
                "method": method,
                "path": path,
                "body": body or {},
            }
            client.sendall(json.dumps(request).encode() + b"\n")
            response = b""
            while not response.endswith(b"\n"):
                response += client.recv(4096)
            return request_id, json.loads(response)
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_accepts_same_uid_and_preserves_request_id(self):
        server = create_unix_server(App(), self.path)
        socket_info = self.path.lstat()

        request_id, response = self._request(server)

        self.assertEqual(stat.S_IMODE(socket_info.st_mode), 0o600)
        self.assertEqual(response["request_id"], request_id)
        self.assertEqual(response["status"], 200)
        self.assertEqual(response["body"], {"status": "ok"})
        self.assertFalse(self.path.exists())

    def test_kernel_peer_credentials_reject_wrong_expected_uid(self):
        server = create_unix_server(
            App(), self.path, peer_policy=PeerCredentialPolicy(expected_uid=os.getuid() + 1)
        )

        _request_id, response = self._request(server)

        self.assertEqual(response["status"], 403)
        self.assertEqual(response["body"]["error"]["code"], "peer_not_authorized")

    def test_passes_kernel_peer_identity_to_execution_layer(self):
        app = App()
        server = create_unix_server(app, self.path)

        _request_id, response = self._request(
            server,
            method="POST",
            path="/v1/plan/execute",
            body={"plan_id": "trusted"},
        )

        self.assertEqual(response["status"], 200)
        context = app.transport_contexts[0]
        self.assertEqual(context.transport, TransportKind.UNIX_PEER)
        self.assertEqual(context.peer_uid, os.getuid())
        self.assertGreater(context.peer_pid, 0)

    def test_policy_rejects_invalid_pid_and_other_uid(self):
        policy = PeerCredentialPolicy(expected_uid=1000, expected_gid=1000)
        self.assertTrue(policy.authorize(PeerCredentials(10, 1000, 1000)))
        self.assertFalse(policy.authorize(PeerCredentials(0, 1000, 1000)))
        self.assertFalse(policy.authorize(PeerCredentials(10, 1001, 1000)))

    def test_removes_owned_stale_socket_but_not_regular_file(self):
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(self.path))
        stale.close()

        server = create_unix_server(App(), self.path)
        server.server_close()

        self.path.write_text("do not replace", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            create_unix_server(App(), self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "do not replace")

    def test_rejects_parent_accessible_by_other_users(self):
        self.root.chmod(0o755)
        with self.assertRaises(PermissionError):
            create_unix_server(App(), self.path)


if __name__ == "__main__":
    unittest.main()
