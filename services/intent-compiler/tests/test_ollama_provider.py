import io
import json
import socket
import unittest
from unittest.mock import patch

from ai_native_intents import (
    ModelRequest,
    OllamaModelProvider,
    OllamaProviderError,
    OllamaUnavailableError,
)


class FakeResponse:
    def __init__(self, payload):
        self.body = io.BytesIO(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.body.read(size)


def model_request():
    return ModelRequest(
        user_text="Найди PDF по математике",
        locale="ru",
        context={"has_active_results": False, "has_last_destination": False, "locale": "ru"},
        output_schema={"type": "object", "additionalProperties": False},
        instructions="Return a validated intent object.",
    )


class OllamaProviderTests(unittest.TestCase):
    def test_sends_closed_schema_with_thinking_disabled(self):
        captured = []

        def open_request(request, timeout):
            captured.append((request, timeout))
            return FakeResponse(
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "search_documents",
                                    "arguments": {
                                        "text": "математика",
                                        "extensions": ["pdf"],
                                        "confidence": 0.95,
                                    },
                                }
                            }
                        ],
                    }
                }
            )

        provider = OllamaModelProvider(timeout_seconds=12, context_tokens=4096)
        with patch("ai_native_intents.ollama._open_loopback", side_effect=open_request):
            result = provider.compile(model_request())

        request, timeout = captured[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(timeout, 12)
        self.assertEqual(body["model"], "qwen3:1.7b")
        self.assertFalse(body["stream"])
        self.assertFalse(body["think"])
        self.assertNotIn("format", body)
        self.assertEqual(len(body["tools"]), 6)
        self.assertEqual(body["options"]["num_ctx"], 4096)
        self.assertEqual(body["options"]["temperature"], 0.7)
        self.assertEqual(body["options"]["top_p"], 0.8)
        self.assertEqual(body["options"]["top_k"], 20)
        self.assertNotIn("<think>", body["messages"][1]["content"])
        self.assertEqual(result["summary"], "Найди PDF по математике")
        self.assertEqual(result["operations"][0]["kind"], "search_documents")
        self.assertEqual(result["operations"][0]["evidence"], ["Найди PDF по математике"])

    def test_health_verifies_version_and_exact_model(self):
        responses = [
            FakeResponse({"version": "0.12.6"}),
            FakeResponse({"models": [{"name": "qwen3:1.7b", "model": "qwen3:1.7b"}]}),
        ]
        with patch("ai_native_intents.ollama._open_loopback", side_effect=responses):
            health = OllamaModelProvider().health()

        self.assertTrue(health.available)
        self.assertTrue(health.model_present)
        self.assertEqual(health.version, "0.12.6")

    def test_builds_compound_dependencies_without_giving_model_trusted_ids(self):
        response = FakeResponse(
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "search_documents",
                                "arguments": {"text": "math", "confidence": 0.9},
                            }
                        },
                        {
                            "function": {
                                "name": "copy_results",
                                "arguments": {"destination": "desktop", "confidence": 0.85},
                            }
                        },
                    ]
                }
            }
        )
        with patch("ai_native_intents.ollama._open_loopback", return_value=response):
            result = OllamaModelProvider().compile(model_request())

        search, copy = result["operations"]
        self.assertEqual(copy["arguments"]["results_from"], search["id"])
        self.assertEqual(copy["depends_on"], [search["id"]])
        self.assertEqual(result["confidence"], 0.85)

    def test_health_reports_missing_model_without_downloading_it(self):
        responses = [FakeResponse({"version": "0.12.6"}), FakeResponse({"models": []})]
        with patch("ai_native_intents.ollama._open_loopback", side_effect=responses):
            health = OllamaModelProvider().health()

        self.assertFalse(health.available)
        self.assertEqual(health.reason, "model_not_installed")

    def test_contains_timeout_and_malformed_output(self):
        provider = OllamaModelProvider()
        with patch("ai_native_intents.ollama._open_loopback", side_effect=socket.timeout()):
            with self.assertRaises(OllamaUnavailableError):
                provider.compile(model_request())
        with patch(
            "ai_native_intents.ollama._open_loopback",
            return_value=FakeResponse({"message": {"content": "not-a-tool-call"}}),
        ):
            with self.assertRaises(OllamaProviderError):
                provider.compile(model_request())

    def test_rejects_remote_or_credentialed_endpoint(self):
        for url in (
            "http://example.com:11434",
            "http://user:password@127.0.0.1:11434",
            "https://127.0.0.1:11434",
            "http://127.0.0.1:11434/api",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                OllamaModelProvider(base_url=url)


if __name__ == "__main__":
    unittest.main()
