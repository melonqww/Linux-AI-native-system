import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]

from ai_native_linux.routing import RuntimeRouter
from ai_native_linux.unix_socket import create_unix_server
from ai_native_query import QueryRuntimeApplication


class InferenceLifecycleRegressionTests(unittest.TestCase):
    def test_runtime_stays_available_when_inference_dependencies_fail(self) -> None:
        def unavailable(*_args, **_kwargs):
            raise RuntimeError("private module failure")

        application = QueryRuntimeApplication(
            object(),
            model_catalog=unavailable,
            ollama_provider_status=unavailable,
        )
        router = RuntimeRouter(application)

        health = router.dispatch("GET", "/v1/health")
        inference = router.dispatch("POST", "/v1/inference/status", {})

        self.assertEqual(health.status, 200)
        self.assertEqual(health.payload, {"status": "ok"})
        self.assertEqual(inference.status, 200)
        self.assertEqual(inference.payload["state"], "error")
        self.assertEqual(
            [model["model_id"] for model in inference.payload["models"]],
            ["workspace.qwen", "assistant.llama", "semantic.selector"],
        )
        self.assertNotIn("private module failure", repr(inference.payload))

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux Unix IPC")
    def test_gnome_runtime_client_can_read_inference_when_health_is_ok(self) -> None:
        gjs = shutil.which("gjs")
        if gjs is None:
            self.skipTest("gjs is not installed")

        application = QueryRuntimeApplication(
            object(),
            model_catalog=lambda: {
                "schema_version": 1,
                "models": [
                    {
                        "model_id": "workspace.qwen",
                        "provider_model": "qwen3.5:2b",
                        "display_name": "Qwen 3.5 2B",
                        "required": True,
                        "state": "ready",
                        "prompt_required": False,
                    },
                    {
                        "model_id": "assistant.llama",
                        "provider_model": "llama3.2:3b",
                        "display_name": "Llama 3.2 3B",
                        "required": False,
                        "state": "declined",
                        "prompt_required": False,
                    },
                ],
            },
            ollama_provider_status=lambda: {
                "schema_version": 1,
                "provider_id": "ollama",
                "display_name": "Ollama",
                "state": "ready",
                "installed": True,
                "prompt_required": False,
            },
        )

        with tempfile.TemporaryDirectory(prefix="ai-native-inference-") as directory:
            socket_path = Path(directory) / "runtime.sock"
            server = create_unix_server(application, socket_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                probe = (
                    PROJECT_ROOT
                    / "apps"
                    / "desktop-panel"
                    / "gnome-extension"
                    / "tests"
                    / "inference_lifecycle_probe.js"
                )
                result = subprocess.run(
                    [gjs, "-m", str(probe), str(socket_path)],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["health"], {"status": "ok"})
        self.assertEqual(payload["inference"]["state"], "ready")
        self.assertEqual(
            [model["effective_state"] for model in payload["inference"]["models"]],
            ["ready", "declined"],
        )


if __name__ == "__main__":
    unittest.main()
