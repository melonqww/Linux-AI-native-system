"""Selection uses the shared provider; it never downloads models."""

import shutil
import unittest
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from ai_native_intents import OllamaModelProvider, OllamaProviderError
from ai_native_linux.workspace_models import WorkspaceModelSelection
from ai_native_linux.routing import RuntimeRouter
from ai_native_query.runtime import QueryRuntimeApplication
from ai_native_workspace import WorkspaceRuntime, WorkspaceBusyError


class WorkspaceModelsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[3] / "tmp" / str(uuid4())
        self.provider = OllamaModelProvider()
        self.default = self.provider.model
        self.installed = [
            {"name": self.default, "size": 100, "digest": "base"},
            {"name": "other:latest", "size": 200, "digest": "other"},
            {"name": "embed:latest", "size": 50, "digest": "embed"},
        ]
        self.calls = []

        def request(method, path, payload=None, **kwargs):
            self.calls.append((method, path, payload))
            if path == "/api/tags":
                return {"models": self.installed}
            if path == "/api/show":
                return {
                    "capabilities": [
                        "embedding"
                        if payload["model"].startswith("embed")
                        else "completion"
                    ]
                }
            if path == "/api/version":
                return {"version": "test"}
            if path == "/api/chat":
                return {"message": {"role": "assistant", "content": '{"choice": 0}'}}
            raise AssertionError(path)

        self.provider._json_request = request
        self.active = []
        self.workspace = WorkspaceRuntime.__new__(WorkspaceRuntime)
        self.workspace.store = SimpleNamespace(list_runs=lambda **kw: self.active)
        self.workspace._configuration_lock = RLock()
        self.selection = WorkspaceModelSelection(
            self.provider, self.workspace, self.root / "model.sqlite3"
        )

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_only_completion_models_and_capabilities_cached_by_digest(self):
        self.assertEqual(
            [m["name"] for m in self.selection.catalog()["models"]],
            ["other:latest", self.default],
        )
        count = len([c for c in self.calls if c[1] == "/api/show"])
        self.selection.catalog()
        self.assertEqual(len([c for c in self.calls if c[1] == "/api/show"]), count)
        self.installed[1]["digest"] = "replacement"
        self.selection.catalog()
        self.assertEqual(len([c for c in self.calls if c[1] == "/api/show"]), count + 1)
        self.assertNotIn("/api/pull", [c[1] for c in self.calls])

    def test_selection_changes_actual_shared_provider_and_survives_restart(self):
        self.selection.select("other:latest")
        self.assertEqual(self.provider.model, "other:latest")
        self.assertEqual(self.selection.status()["state"], "ready")
        self.provider.summarize_result(
            {"state": "completed", "found_items": 0, "completed_steps": 1}, locale="en"
        )
        self.assertEqual(
            [c[2]["model"] for c in self.calls if c[1] == "/api/chat"], ["other:latest"]
        )
        restored = OllamaModelProvider()
        WorkspaceModelSelection(restored, self.workspace, self.root / "model.sqlite3")
        self.assertEqual(restored.model, "other:latest")

    def test_unknown_embedding_and_removed_models_are_rejected(self):
        for name in ("missing:latest", "embed:latest"):
            with self.assertRaises(ValueError):
                self.selection.select(name)
        self.installed = [m for m in self.installed if m["name"] != "other:latest"]
        with self.assertRaises(ValueError):
            self.selection.select("other:latest")
        self.assertEqual(self.provider.model, self.default)

    def test_busy_workspace_does_not_change_model_or_persist_choice(self):
        self.active = [SimpleNamespace(stage="awaiting_approval")]
        self.assertTrue(self.selection.catalog()["busy"])
        with self.assertRaises(WorkspaceBusyError):
            self.selection.select("other:latest")
        self.assertEqual(self.provider.model, self.default)
        restored = OllamaModelProvider()
        WorkspaceModelSelection(restored, self.workspace, self.root / "model.sqlite3")
        self.assertEqual(restored.model, self.default)
        self.active = []
        self.selection.select("other:latest")

    def test_removed_selected_model_has_no_download_fallback(self):
        self.selection.select("other:latest")
        self.installed = []
        self.assertEqual(self.selection.status()["state"], "unavailable")
        self.assertNotIn("/api/pull", [c[1] for c in self.calls])

    def test_routes_require_secure_transport_and_report_conflicts(self):
        app = QueryRuntimeApplication(Mock(), workspace_models=self.selection)
        for path in ("/v1/workspace/models", "/v1/workspace/model/select"):
            response = RuntimeRouter(app, allow_r1=False).dispatch("POST", path, {})
            self.assertEqual(response.status, 403)
        router = RuntimeRouter(app)
        self.assertEqual(
            router.dispatch("POST", "/v1/workspace/models", {}).status, 200
        )
        self.assertEqual(
            router.dispatch(
                "POST", "/v1/workspace/model/select", {"name": "missing"}
            ).status,
            400,
        )
        self.active = [object()]
        self.assertEqual(
            router.dispatch(
                "POST", "/v1/workspace/model/select", {"name": "other:latest"}
            ).status,
            409,
        )
        self.active = []
        self.assertEqual(
            router.dispatch(
                "POST", "/v1/workspace/model/select", {"name": "other:latest"}
            ).status,
            200,
        )

    def test_invalid_model_metadata_rejected(self):
        self.installed[0]["digest"] = []
        with self.assertRaises(OllamaProviderError):
            self.provider.installed_models()

    def test_lifecycle_uses_selected_model_without_base_download_prompt(self):
        self.selection.select("other:latest")
        catalog = {
            "models": [
                {
                    "model_id": "workspace.qwen",
                    "provider_model": self.default,
                    "role": "workspace_base",
                    "required": True,
                    "state": "consent_required",
                    "prompt_required": True,
                },
                {"model_id": "assistant.llama", "state": "declined", "required": False},
                {
                    "model_id": "semantic.selector",
                    "state": "declined",
                    "required": False,
                },
            ]
        }
        app = QueryRuntimeApplication(
            Mock(),
            workspace_models=self.selection,
            model_catalog=lambda: catalog,
            ollama_provider_status=lambda: {"state": "ready"},
        )
        status = app.inference_lifecycle({})
        self.assertEqual(status["active_model"], "other:latest")
        self.assertEqual(status["state"], "ready")
        self.assertFalse(status["models"][0]["prompt_required"])
        self.assertEqual(status["models"][-1]["effective_state"], "ready")
