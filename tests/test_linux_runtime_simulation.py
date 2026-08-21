import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from ai_native_capabilities import CapabilityRegistry
from ai_native_linux.routing import RuntimeRouter
from ai_native_module_manager import ModuleProcessManager
from ai_native_query import QueryRuntimeApplication


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LinuxRuntimeSimulationTests(unittest.TestCase):
    """Exercise the Linux-style core/module boundary without a real desktop."""

    def test_core_and_inference_lifecycle_survive_real_module_workers(self) -> None:
        root = PROJECT_ROOT / "tmp" / "linux-runtime-simulation-tests" / str(uuid4())
        root.mkdir(parents=True)
        manager = None
        try:
            registry = CapabilityRegistry(root / "registry.sqlite3")
            report = registry.sync([PROJECT_ROOT / "services", PROJECT_ROOT / "modules"])
            self.assertEqual(report.issues, ())

            manager = ModuleProcessManager(
                registry,
                idle_seconds=0,
                runtime_directory=root / "runtime",
            )
            provider_module = manager.start_for_capability("provider.ollama.status")
            model_module = manager.start_for_capability("model.local.ensure")

            self.assertTrue(manager.health(provider_module))
            self.assertTrue(manager.health(model_module))

            application = QueryRuntimeApplication(
                object(),
                model_catalog=lambda: manager.invoke(
                    model_module, "catalog", timeout=15
                ),
                ollama_provider_status=lambda: manager.invoke(
                    provider_module, "status", timeout=15
                ),
            )
            router = RuntimeRouter(application)

            health = router.dispatch("GET", "/v1/health")
            inference = router.dispatch("POST", "/v1/inference/status", {})

            self.assertEqual(health.status, 200)
            self.assertEqual(health.payload, {"status": "ok"})
            self.assertEqual(inference.status, 200)
            self.assertIn(
                inference.payload["state"],
                {
                    "action_required",
                    "provider_preparing",
                    "models_preparing",
                    "ready",
                    "blocked",
                    "error",
                },
            )
            self.assertEqual(
                {model["model_id"] for model in inference.payload["models"]},
                {"workspace.qwen", "assistant.llama"},
            )
            self.assertNotIn("Traceback", repr(inference.payload))
        finally:
            if manager is not None:
                manager.stop_all()
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
