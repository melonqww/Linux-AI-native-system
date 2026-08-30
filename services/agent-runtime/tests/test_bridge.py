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
class SearchPage:
    results: tuple[Result, ...]
    offset: int
    limit: int
    total_matches: int
    total_is_exact: bool
    coverage: dict


@dataclass
class Compilation:
    state: str
    text: str


@dataclass
class Execution:
    state: str
    plan_id: str


@dataclass
class Task:
    task_id: str
    state: str


class App:
    def capabilities(self):
        return [
            "storage.catalog.search",
            "execution.r1.copy",
            "workspace.messages.read",
            "workspace.runs.read",
            "workspace.submit",
            "workspace.approval.respond",
            "models.catalog.read",
            "models.lifecycle.respond",
            "providers.ollama.read",
            "providers.ollama.respond",
            "inference.lifecycle.read",
            "storage.volumes.read",
            "storage.volumes.enroll",
            "software.catalog.read",
            "software.tasks.read",
            "software.backups.read",
            "software.install.prepare",
            "software.install.commit",
            "software.remove.prepare",
            "software.remove.commit",
            "software.tasks.control",
        ]

    def search(self, payload):
        return [Result(path=f"result:{payload['text']}")]

    def search_page(self, payload):
        return SearchPage(
            (Result(path=f"result:{payload['text']}"),),
            int(payload.get("offset", 0)),
            int(payload.get("limit", 20)),
            1,
            True,
            {"complete": True},
        )

    def index_status(self):
        return {"scheduler": {"state": "idle", "queued": 0}}

    def system_status(self, payload=None):
        return {
            "schema_version": 1,
            "supported": True,
            "process_sort": (payload or {}).get("process_sort", "cpu"),
            "processes": [],
        }

    def check_system_updates(self, payload):
        if payload:
            raise ValueError("unexpected payload")
        return {"schema_version": 1, "state": "updates_available", "available_count": 2}

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

    def tasks(self):
        return (Task(task_id="task-1", state="completed"),)

    def task_detail(self, payload):
        return Task(task_id=payload["task_id"], state="completed")

    def cancel_task(self, payload):
        return Task(task_id=payload["task_id"], state="running")

    def continue_task(self, payload):
        return Task(task_id=payload["task_id"], state="interrupted")

    def model_catalog(self, payload):
        return {"schema_version": 1, "models": []}

    def respond_to_model(self, payload):
        return {"model_id": payload["model_id"], "decision": payload["decision"]}

    def ollama_provider_status(self, payload):
        return {"provider_id": "ollama", "state": "consent_required"}

    def respond_to_ollama_provider(self, payload):
        return {"provider_id": "ollama", "decision": payload["decision"]}

    def inference_lifecycle(self, payload):
        return {"schema_version": 1, "state": "action_required", "models": []}

    def storage_volumes(self, payload):
        return {"schema_version": 1, "volumes": []}

    def storage_permission(self, payload):
        return payload

    def software_snapshot(self, payload):
        if payload:
            raise ValueError("unexpected payload")
        return {"schema_version": 1, "catalog": [], "tasks": [], "backups": []}

    def software_prepare(self, payload, *, transport_context):
        return {"schema_version": 1, "task": payload}

    def software_respond(self, payload, *, transport_context):
        return {"schema_version": 1, "task": payload}

    def software_control(self, payload, *, transport_context):
        return {"schema_version": 1, "task": payload}


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
            system_status = json.load(urllib.request.urlopen(base + "/v1/system-status"))
            monitor_request = urllib.request.Request(
                base + "/v1/system-status",
                data=json.dumps({"process_sort": "memory"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            memory_status = json.load(urllib.request.urlopen(monitor_request))
            updates_request = urllib.request.Request(
                base + "/v1/system-updates/check",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            updates = json.load(urllib.request.urlopen(updates_request))
            tasks = json.load(urllib.request.urlopen(base + "/v1/tasks"))
            request = urllib.request.Request(
                base + "/v1/search",
                data=json.dumps({"text": "math"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            results = json.load(urllib.request.urlopen(request))
            page_request = urllib.request.Request(
                base + "/v1/search-page",
                data=json.dumps({"text": "math", "offset": 0, "limit": 10}).encode(),
                headers={"Content-Type": "application/json"},
            )
            page = json.load(urllib.request.urlopen(page_request))
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
            cancel_request = urllib.request.Request(
                base + "/v1/tasks/cancel",
                data=json.dumps({"task_id": "task-1"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as cancel_error:
                urllib.request.urlopen(cancel_request)
            workspace_request = urllib.request.Request(
                base + "/v1/workspace/messages",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as workspace_error:
                urllib.request.urlopen(workspace_request)
            submit_request = urllib.request.Request(
                base + "/v1/workspace/submit",
                data=json.dumps({"text": "hello"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as submit_error:
                urllib.request.urlopen(submit_request)
            models_request = urllib.request.Request(
                base + "/v1/models/catalog",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as models_error:
                urllib.request.urlopen(models_request)
            provider_request = urllib.request.Request(
                base + "/v1/providers/ollama/status",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as provider_error:
                urllib.request.urlopen(provider_request)
            inference_request = urllib.request.Request(
                base + "/v1/inference/status",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as inference_error:
                urllib.request.urlopen(inference_request)
            storage_request = urllib.request.Request(
                base + "/v1/storage/volumes",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as storage_error:
                urllib.request.urlopen(storage_request)
            software_request = urllib.request.Request(
                base + "/v1/software/snapshot",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as software_error:
                urllib.request.urlopen(software_request)
            software_prepare_request = urllib.request.Request(
                base + "/v1/software/prepare",
                data=json.dumps({"application_id": "steam", "action": "install"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as software_prepare_error:
                urllib.request.urlopen(software_prepare_request)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(health, {"status": "ok"})
        self.assertNotIn("execution.r1.copy", capabilities["capabilities"])
        self.assertNotIn("workspace.messages.read", capabilities["capabilities"])
        self.assertNotIn("workspace.runs.read", capabilities["capabilities"])
        self.assertNotIn("workspace.submit", capabilities["capabilities"])
        self.assertNotIn("models.catalog.read", capabilities["capabilities"])
        self.assertNotIn("models.lifecycle.respond", capabilities["capabilities"])
        self.assertNotIn("providers.ollama.read", capabilities["capabilities"])
        self.assertNotIn("providers.ollama.respond", capabilities["capabilities"])
        self.assertNotIn("inference.lifecycle.read", capabilities["capabilities"])
        self.assertNotIn("storage.volumes.read", capabilities["capabilities"])
        self.assertNotIn("storage.volumes.enroll", capabilities["capabilities"])
        self.assertNotIn("software.catalog.read", capabilities["capabilities"])
        self.assertNotIn("software.tasks.read", capabilities["capabilities"])
        self.assertNotIn("software.backups.read", capabilities["capabilities"])
        self.assertNotIn("software.install.prepare", capabilities["capabilities"])
        self.assertNotIn("software.install.commit", capabilities["capabilities"])
        self.assertNotIn("software.remove.prepare", capabilities["capabilities"])
        self.assertNotIn("software.remove.commit", capabilities["capabilities"])
        self.assertNotIn("software.tasks.control", capabilities["capabilities"])
        self.assertEqual(status["scheduler"]["state"], "idle")
        self.assertEqual(system_status["schema_version"], 1)
        self.assertTrue(system_status["supported"])
        self.assertEqual(system_status["process_sort"], "cpu")
        self.assertEqual(memory_status["process_sort"], "memory")
        self.assertEqual(updates["state"], "updates_available")
        self.assertEqual(updates["available_count"], 2)
        self.assertEqual(tasks, {"tasks": [{"task_id": "task-1", "state": "completed"}]})
        self.assertEqual(results["results"][0]["path"], "result:math")
        self.assertEqual(page["results"][0]["path"], "result:math")
        self.assertEqual(page["total_matches"], 1)
        self.assertEqual(compilation, {"state": "ready", "text": "find math PDFs"})
        self.assertEqual(execution, {"state": "completed", "plan_id": "trusted-plan"})
        self.assertEqual(approval_error.exception.code, 403)
        self.assertEqual(approval["error"]["code"], "secure_transport_required")
        self.assertEqual(cancel_error.exception.code, 403)
        self.assertEqual(storage_error.exception.code, 403)
        self.assertEqual(software_error.exception.code, 403)
        self.assertEqual(software_prepare_error.exception.code, 403)
        self.assertEqual(workspace_error.exception.code, 403)
        self.assertEqual(submit_error.exception.code, 403)
        self.assertEqual(models_error.exception.code, 403)
        self.assertEqual(provider_error.exception.code, 403)
        self.assertEqual(inference_error.exception.code, 403)

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
