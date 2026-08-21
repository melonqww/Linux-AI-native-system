import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch


PROBE_PATH = Path(__file__).resolve().parents[1] / "runtime_probe.py"
SPEC = importlib.util.spec_from_file_location("runtime_probe", PROBE_PATH)
assert SPEC is not None and SPEC.loader is not None
runtime_probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime_probe)


class RuntimeProbeTests(unittest.TestCase):
    def test_decodes_valid_runtime_envelope(self) -> None:
        request_id = "5f2affd2-ef31-4499-a330-8bc7d41e39c0"
        raw = json.dumps(
            {
                "version": 1,
                "request_id": request_id,
                "status": 200,
                "body": {"state": "action_required"},
            }
        ).encode()

        self.assertEqual(
            runtime_probe.decode_response(raw, request_id),
            {"state": "action_required"},
        )

    def test_rejects_runtime_error_without_private_details(self) -> None:
        request_id = "5f2affd2-ef31-4499-a330-8bc7d41e39c0"
        raw = json.dumps(
            {
                "version": 1,
                "request_id": request_id,
                "status": 500,
                "body": {
                    "error": {
                        "code": "internal_error",
                        "retryable": True,
                    }
                },
            }
        ).encode()

        with self.assertRaisesRegex(
            runtime_probe.ProbeError,
            "runtime_status_500:internal_error",
        ):
            runtime_probe.decode_response(raw, request_id)

    def test_rejects_mismatched_request(self) -> None:
        raw = json.dumps(
            {
                "version": 1,
                "request_id": "different",
                "status": 200,
                "body": {},
            }
        ).encode()

        with self.assertRaisesRegex(
            runtime_probe.ProbeError,
            "response_contract_invalid",
        ):
            runtime_probe.decode_response(raw, "expected")

    def test_probe_rejects_missing_lifecycle_capability(self) -> None:
        with patch.object(
            runtime_probe,
            "request",
            side_effect=(
                {"status": "ok"},
                {"capabilities": []},
                {
                    "state": "error",
                    "provider": {"state": "error"},
                    "models": [
                        {"model_id": "workspace.qwen"},
                        {"model_id": "assistant.llama"},
                    ],
                    "errors": [],
                },
            ),
        ):
            with self.assertRaisesRegex(
                runtime_probe.ProbeError,
                "inference_lifecycle_capability_missing",
            ):
                runtime_probe.probe(Path("runtime.sock"))

    def test_probe_reports_public_component_failure(self) -> None:
        with patch.object(
            runtime_probe,
            "request",
            side_effect=(
                {"status": "ok"},
                {"capabilities": ["inference.lifecycle.read"]},
                {
                    "state": "error",
                    "provider": {"state": "error"},
                    "models": [
                        {"model_id": "workspace.qwen"},
                        {"model_id": "assistant.llama"},
                    ],
                    "errors": [
                        {
                            "component": "provider.ollama",
                            "code": "status_unavailable",
                        }
                    ],
                },
            ),
        ):
            with self.assertRaisesRegex(
                runtime_probe.ProbeError,
                "provider.ollama:status_unavailable",
            ):
                runtime_probe.probe(Path("runtime.sock"))


if __name__ == "__main__":
    unittest.main()
