import shutil
import sqlite3
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from ai_native_model_ollama import (
    ModelDecisionStore,
    ModelDefinition,
    ModelStatus,
    OllamaModelCatalog,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class FakeManager:
    installed_models: set[str] = set()
    ensure_calls: list[str] = []

    def __init__(self, *, model, base_url, lifecycle_lock):
        self.model = model
        self.lifecycle_lock = lifecycle_lock

    def installed(self):
        return self.model in self.installed_models

    def ensure(self):
        self.ensure_calls.append(self.model)
        state = "ready" if self.installed() else "starting"
        return self._status(state, None, True)

    def policy_status(self, state, *, reason=None):
        return self._status(state, reason, False)

    def stop(self):
        return None

    def _status(self, state, reason, auto_download):
        return ModelStatus(
            1, "ollama", self.model, state, None, 0, None, reason, auto_download
        )


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 8, 21, 12, tzinfo=UTC)

    def __call__(self):
        return self.value


class ModelCatalogTests(unittest.TestCase):
    def setUp(self):
        self.root = PROJECT_ROOT / "tmp" / "model-catalog-tests" / str(uuid4())
        self.root.mkdir(parents=True)
        self.clock = MutableClock()
        FakeManager.installed_models = set()
        FakeManager.ensure_calls = []

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def catalog(self):
        return OllamaModelCatalog(
            (
                ModelDefinition("workspace.qwen", "qwen3.5:2b", "Qwen", "base", True),
                ModelDefinition(
                    "assistant.llama", "llama3.2:3b", "Llama", "optional", False
                ),
            ),
            ModelDecisionStore(self.root / "decisions.sqlite3"),
            base_url="http://127.0.0.1:11434",
            now_fn=self.clock,
            manager_factory=FakeManager,
        )

    def test_missing_models_require_independent_consent_without_download(self):
        catalog = self.catalog()

        models = catalog.catalog()["models"]
        base = catalog.ensure_base()

        self.assertEqual([item["model_id"] for item in models], [
            "workspace.qwen", "assistant.llama"
        ])
        self.assertTrue(all(item["prompt_required"] for item in models))
        self.assertEqual(base["state"], "consent_required")
        self.assertFalse(base["auto_download"])
        self.assertEqual(FakeManager.ensure_calls, [])

    def test_download_decision_starts_only_selected_model_and_persists(self):
        catalog = self.catalog()

        view = catalog.respond("assistant.llama", "download")
        reopened = self.catalog().catalog()["models"][1]

        self.assertEqual(view["decision"], "download")
        self.assertEqual(FakeManager.ensure_calls, ["llama3.2:3b", "llama3.2:3b"])
        self.assertFalse(reopened["prompt_required"])
        self.assertEqual(reopened["state"], "starting")

    def test_later_suppresses_prompt_for_24_hours_then_prompts_again(self):
        catalog = self.catalog()
        catalog.respond("workspace.qwen", "later")

        deferred = catalog.ensure_base()
        self.clock.value += timedelta(hours=24, seconds=1)
        expired = catalog.ensure_base()

        self.assertEqual(deferred["state"], "deferred")
        self.assertEqual(expired["state"], "consent_required")

    def test_never_is_persistent_but_installed_model_is_still_ready(self):
        catalog = self.catalog()
        catalog.respond("workspace.qwen", "never")
        self.assertEqual(self.catalog().ensure_base()["state"], "declined")

        FakeManager.installed_models.add("qwen3.5:2b")
        self.assertEqual(self.catalog().ensure_base()["state"], "ready")

    def test_model_upgrade_requires_fresh_consent_and_migrates_old_database(self):
        database = self.root / "decisions.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                """CREATE TABLE model_decisions (
                    model_id TEXT PRIMARY KEY,
                    decision TEXT NOT NULL,
                    dismissed_until TEXT,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                "INSERT INTO model_decisions VALUES (?, ?, ?, ?)",
                ("workspace.qwen", "download", None, self.clock().isoformat()),
            )
            connection.execute("PRAGMA user_version = 1")

        catalog = OllamaModelCatalog(
            (ModelDefinition("workspace.qwen", "qwen3.5:2b", "Qwen", "base", True),),
            ModelDecisionStore(database),
            base_url="http://127.0.0.1:11434",
            now_fn=self.clock,
            manager_factory=FakeManager,
        )

        state = catalog.ensure_base()
        self.assertEqual(state["state"], "consent_required")
        self.assertEqual(FakeManager.ensure_calls, [])
        with sqlite3.connect(database) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(model_decisions)")
            }
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertIn("provider_model", columns)
        self.assertEqual(version, 2)

    def test_rejects_unknown_model_and_decision(self):
        catalog = self.catalog()
        with self.assertRaises(ValueError):
            catalog.respond("unknown", "download")
        with self.assertRaises(ValueError):
            catalog.respond("workspace.qwen", "maybe")


if __name__ == "__main__":
    unittest.main()
