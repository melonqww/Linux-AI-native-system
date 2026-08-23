import unittest

from ai_native_query import workspace_model_readiness


class WorkspaceModelReadinessTests(unittest.TestCase):
    def test_model_is_not_called_until_provider_api_is_ready(self):
        model_calls = []
        result = workspace_model_readiness(
            lambda: {
                "state": "error",
                "installed": True,
                "reason": "external_server_unavailable",
            },
            lambda: model_calls.append(True) or {"state": "ready"},
        )

        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["component"], "provider.ollama")
        self.assertEqual(result["reason"], "external_server_unavailable")
        self.assertEqual(model_calls, [])

    def test_provider_preparation_has_stable_workspace_state(self):
        result = workspace_model_readiness(
            lambda: {"state": "starting", "reason": None},
            lambda: {"state": "ready"},
        )

        self.assertEqual(result["state"], "starting")
        self.assertEqual(result["reason"], "provider_preparing")

    def test_ready_provider_delegates_to_model_lifecycle(self):
        model = {"state": "ready", "model": "qwen3.5:2b"}
        result = workspace_model_readiness(
            lambda: {"state": "ready", "installed": True},
            lambda: model,
        )

        self.assertIs(result, model)

    def test_component_failures_do_not_leak_details(self):
        result = workspace_model_readiness(
            lambda: (_ for _ in ()).throw(RuntimeError("secret provider failure")),
            lambda: {"state": "ready"},
        )

        self.assertEqual(result["reason"], "provider_status_unavailable")
        self.assertNotIn("secret", repr(result))


if __name__ == "__main__":
    unittest.main()
