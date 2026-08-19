import json
import sys
import threading
import unittest
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_native_linux.bridge import create_server


@dataclass
class Result:
    path: str


@dataclass
class Compilation:
    state: str
    text: str


@dataclass
class Execution:
    state: str
    plan_id: str


class App:
    def capabilities(self):
        return ["storage.catalog.search", "execution.r1.copy"]

    def search(self, payload):
        return [Result(path=f"result:{payload['text']}")]

    def index_status(self):
        return {"scheduler": {"state": "idle", "queued": 0}}

    def compile_intent(self, payload):
        return Compilation(state="ready", text=payload["text"])

    def execute_plan(self, payload, *, transport_context):
        if payload["plan_id"] == "explode":
            raise LookupError("sensitive internal detail")
        return Execution(state="completed", plan_id=payload["plan_id"])

    def respond_to_approval(self, payload, *, transport_context):
        return Execution(
            state="completed" if payload["confirmed"] else "cancelled",
            plan_id=payload["approval_request_id"],
        )


class BridgeTests(unittest.TestCase):
    def test_rejects_non_loopback_binding(self):
        with self.assertRaises(ValueError):
            create_server(App(), host="0.0.0.0")

    def test_health_capabilities_and_search(self):
        server = create_server(App())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            health = json.load(urllib.request.urlopen(base + "/v1/health"))
            capabilities = json.load(urllib.request.urlopen(base + "/v1/capabilities"))
            status = json.load(urllib.request.urlopen(base + "/v1/index-status"))
            request = urllib.request.Request(
                base + "/v1/search",
                data=json.dumps({"text": "math"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            results = json.load(urllib.request.urlopen(request))
            intent_request = urllib.request.Request(
                base + "/v1/intent/compile",
                data=json.dumps({"text": "find math PDFs"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            compilation = json.load(urllib.request.urlopen(intent_request))
            execution_request = urllib.request.Request(
                base + "/v1/plan/execute",
                data=json.dumps({"plan_id": "trusted-plan"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            execution = json.load(urllib.request.urlopen(execution_request))
            approval_request = urllib.request.Request(
                base + "/v1/approval/respond",
                data=json.dumps(
                    {"approval_request_id": "approval-1", "confirmed": False}
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as approval_error:
                urllib.request.urlopen(approval_request)
            approval = json.load(approval_error.exception)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(health, {"status": "ok"})
        self.assertNotIn("execution.r1.copy", capabilities["capabilities"])
        self.assertEqual(status["scheduler"]["state"], "idle")
        self.assertEqual(results["results"][0]["path"], "result:math")
        self.assertEqual(compilation, {"state": "ready", "text": "find math PDFs"})
        self.assertEqual(execution, {"state": "completed", "plan_id": "trusted-plan"})
        self.assertEqual(approval_error.exception.code, 403)
        self.assertEqual(approval["error"]["code"], "secure_transport_required")

    def test_global_error_envelope_redacts_unexpected_failures(self):
        server = create_server(App())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/plan/execute",
                data=json.dumps({"plan_id": "explode"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request)
            body = json.load(raised.exception)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(raised.exception.code, 500)
        self.assertEqual(body["error"]["code"], "internal_error")
        self.assertTrue(body["error"]["retryable"])
        self.assertIn("request_id", body["error"])
        self.assertNotIn("sensitive internal detail", json.dumps(body))
