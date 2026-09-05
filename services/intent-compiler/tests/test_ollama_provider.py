import io
import json
import socket
import unittest
from unittest.mock import patch

from ai_native_intents import (
    ModelHistoryMessage,
    ModelRequest,
    ModelTurnKind,
    OllamaModelProvider,
    OllamaProviderError,
    OllamaUnavailableError,
)
from ai_native_turns import TurnHistoryMessage, TurnRequest


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


def model_request(user_text="Найди PDF по математике"):
    return ModelRequest(
        user_text=user_text,
        locale="ru",
        context={
            "has_active_results": False,
            "has_last_destination": False,
            "locale": "ru",
        },
        output_schema={"type": "object", "additionalProperties": False},
        instructions="Return a validated intent object.",
    )


class OllamaProviderTests(unittest.TestCase):
    def test_classifies_mixed_turn_with_closed_schema_and_exact_fragments(self):
        response = FakeResponse(
            {
                "message": {
                    "content": json.dumps(
                        {
                            "kind": "mixed",
                            "language": "ru",
                            "confidence": 0.96,
                            "conversation_text": "Расскажи про хлеб",
                            "action_text": "найди PDF",
                        },
                        ensure_ascii=False,
                    )
                }
            }
        )
        captured = []

        def open_request(request, _timeout):
            captured.append(json.loads(request.data))
            return response

        with patch("ai_native_intents.ollama._open_loopback", side_effect=open_request):
            result = OllamaModelProvider().classify_turn(
                TurnRequest(
                    "Расскажи про хлеб и найди PDF",
                    "ru",
                    (TurnHistoryMessage("user", "Привет"),),
                )
            )

        self.assertEqual(result["kind"], "mixed")
        self.assertNotIn("tools", captured[0])
        self.assertEqual(captured[0]["format"], "json")
        self.assertFalse(captured[0]["think"])

    def test_chat_has_no_tools_and_exposes_previous_user_message_as_context(self):
        response = FakeResponse({"message": {"content": "Ты спрашивал про хлеб."}})
        captured = []

        def open_request(request, _timeout):
            captured.append(json.loads(request.data))
            return response

        request = model_request("Какое было моё прошлое сообщение?")
        request = ModelRequest(
            request.user_text,
            request.locale,
            request.context,
            {},
            "",
            history=(
                ModelHistoryMessage("user", "Какая температура нужна для хлеба?"),
            ),
        )
        with patch("ai_native_intents.ollama._open_loopback", side_effect=open_request):
            reply = OllamaModelProvider().respond_chat(request)

        self.assertEqual(reply, "Ты спрашивал про хлеб.")
        self.assertNotIn("tools", captured[0])
        self.assertNotIn("format", captured[0])
        self.assertIn(
            "Previous user message: Какая температура",
            captured[0]["messages"][-1]["content"],
        )

    def test_metadata_file_listing_omits_content_text(self):
        response = FakeResponse(
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "search_documents",
                                "arguments": {
                                    "mode": "metadata",
                                    "extensions": ["pdf"],
                                    "confidence": 0.98,
                                },
                            }
                        }
                    ],
                }
            }
        )
        with patch("ai_native_intents.ollama._open_loopback", return_value=response):
            turn = OllamaModelProvider().route(model_request("Найди все PDF"))
        self.assertEqual(
            turn.intent_payload["operations"][0]["arguments"],
            {"mode": "metadata", "extensions": ["pdf"]},
        )

    def test_candidate_operations_limit_tools_exposed_to_qwen(self):
        response = FakeResponse(
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "search_documents",
                                "arguments": {
                                    "mode": "metadata",
                                    "extensions": ["pdf"],
                                    "confidence": 0.98,
                                },
                            }
                        }
                    ],
                }
            }
        )
        captured = []

        def open_request(request, _timeout):
            captured.append(json.loads(request.data))
            return response

        request = model_request("Найди PDF")
        request = ModelRequest(
            request.user_text,
            request.locale,
            request.context,
            request.output_schema,
            request.instructions,
            allowed_operations=("search_documents",),
        )
        with patch("ai_native_intents.ollama._open_loopback", side_effect=open_request):
            turn = OllamaModelProvider().route(request)

        names = [tool["function"]["name"] for tool in captured[0]["tools"]]
        self.assertEqual(names, ["search_documents"])
        properties = captured[0]["tools"][0]["function"]["parameters"]["properties"]
        self.assertIn(
            "inside indexed document content", properties["text"]["description"]
        )
        self.assertIn("never content", properties["name_terms"]["description"])
        self.assertEqual(turn.kind, ModelTurnKind.ACTION)

    def test_rejects_hallucinated_tool_outside_candidate_set(self):
        response = FakeResponse(
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "copy_results",
                                "arguments": {
                                    "destination": "desktop",
                                    "confidence": 0.8,
                                },
                            }
                        }
                    ],
                }
            }
        )
        request = model_request("Найди PDF")
        request = ModelRequest(
            request.user_text,
            request.locale,
            request.context,
            request.output_schema,
            request.instructions,
            allowed_operations=("search_documents",),
        )
        with patch("ai_native_intents.ollama._open_loopback", return_value=response):
            with self.assertRaises(OllamaProviderError):
                OllamaModelProvider().route(request)

    def test_missing_copy_destination_preserves_a_non_executable_draft(self):
        response = FakeResponse(
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "copy_results",
                                "arguments": {
                                    "destination": "documents",
                                    "directory_name": "Private",
                                    "confidence": 0.9,
                                },
                            }
                        }
                    ],
                }
            }
        )
        request = model_request("Copy those results to a folder called Private")
        request = ModelRequest(
            request.user_text,
            "en",
            {"has_active_results": True, "has_last_destination": False, "locale": "en"},
            request.output_schema,
            request.instructions,
            allowed_operations=("copy_results",),
        )
        with patch("ai_native_intents.ollama._open_loopback", return_value=response):
            turn = OllamaModelProvider().route(request)

        self.assertEqual(turn.kind, ModelTurnKind.CLARIFICATION)
        operation = turn.pending_intent_payload["operations"][0]
        self.assertEqual(operation["arguments"]["directory_name"], "Private")
        self.assertEqual(
            operation["arguments"]["results_from"], "context.active_results"
        )
        self.assertNotIn("destination", operation["arguments"])

    def test_composes_only_conversational_part_of_mixed_turn(self):
        response = FakeResponse(
            {
                "message": {
                    "content": '{"conversation_reply":"Для хлеба уточните рецепт."}'
                }
            }
        )
        with patch("ai_native_intents.ollama._open_loopback", return_value=response):
            reply = OllamaModelProvider().compose_conversation(
                model_request("Подскажи про хлеб и найди PDF"),
                system_result="Поиск завершён. Найдено файлов: 2.",
            )
        self.assertEqual(reply, "Для хлеба уточните рецепт.")

    def test_routes_greeting_to_conversation_without_an_execution_intent(self):
        response = FakeResponse(
            {"message": {"role": "assistant", "content": "Привет! Чем помочь?"}}
        )
        provider = OllamaModelProvider()

        with patch("ai_native_intents.ollama._open_loopback", return_value=response):
            turn = provider.route(model_request("Привет"))

        self.assertEqual(turn.kind, ModelTurnKind.CONVERSATION)
        self.assertEqual(turn.response_text, "Привет! Чем помочь?")
        self.assertIsNone(turn.intent_payload)

    def test_intent_only_compilation_rejects_conversation(self):
        response = FakeResponse({"message": {"content": "Привет!"}})
        with patch("ai_native_intents.ollama._open_loopback", return_value=response):
            with self.assertRaises(OllamaProviderError):
                OllamaModelProvider().compile(model_request("Привет"))

    def test_summarizes_only_from_bounded_confirmed_facts(self):
        response = FakeResponse({"message": {"content": '{"choice":0}'}})
        captured = []

        def open_request(request, timeout):
            captured.append(json.loads(request.data))
            return response

        with patch("ai_native_intents.ollama._open_loopback", side_effect=open_request):
            summary = OllamaModelProvider().summarize_result(
                {"state": "completed", "found_items": 4}, locale="ru"
            )

        self.assertEqual(summary, "Готово. Найдено объектов: 4.")
        self.assertFalse(captured[0]["think"])
        self.assertNotIn("tools", captured[0])
        self.assertEqual(captured[0]["format"]["properties"]["choice"]["enum"], [0, 1])
        self.assertEqual(captured[0]["options"]["temperature"], 0.2)

    def test_rejects_summary_with_an_invented_number(self):
        response = FakeResponse({"message": {"content": '{"choice":99}'}})
        with patch("ai_native_intents.ollama._open_loopback", return_value=response):
            with self.assertRaises(OllamaProviderError):
                OllamaModelProvider().summarize_result(
                    {"state": "completed", "found_items": 4}, locale="ru"
                )

    def test_rejects_non_numeric_but_unconfirmed_summary_claim(self):
        response = FakeResponse({"message": {"content": "Готово. Все файлы удалены."}})
        with patch("ai_native_intents.ollama._open_loopback", return_value=response):
            with self.assertRaises(OllamaProviderError):
                OllamaModelProvider().summarize_result(
                    {"state": "completed", "found_items": 0}, locale="ru"
                )

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
        self.assertEqual(body["model"], "qwen3.5:2b")
        self.assertFalse(body["stream"])
        self.assertFalse(body["think"])
        self.assertNotIn("format", body)
        self.assertEqual(len(body["tools"]), 7)
        search_tool = next(
            item["function"]
            for item in body["tools"]
            if item["function"]["name"] == "search_documents"
        )
        self.assertNotIn("languages", search_tool["parameters"]["properties"])
        self.assertIn("Never drop", search_tool["description"])
        self.assertEqual(body["options"]["num_ctx"], 4096)
        self.assertEqual(body["options"]["temperature"], 0.7)
        self.assertEqual(body["options"]["top_p"], 0.8)
        self.assertEqual(body["options"]["top_k"], 20)
        self.assertNotIn("<think>", body["messages"][1]["content"])
        self.assertEqual(result["summary"], "Найди PDF по математике")
        self.assertEqual(result["operations"][0]["kind"], "search_documents")
        self.assertEqual(
            result["operations"][0]["evidence"], ["Найди PDF по математике"]
        )

    def test_action_route_excludes_untrusted_history_and_preserves_reply(self):
        captured = []

        def open_request(request, _timeout):
            captured.append(json.loads(request.data))
            return FakeResponse(
                {
                    "message": {
                        "content": "Понял, подготовлю поиск.",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "search_documents",
                                    "arguments": {
                                        "text": "математика",
                                        "confidence": 0.9,
                                    },
                                }
                            }
                        ],
                    }
                }
            )

        request = model_request("Найди их")
        request = ModelRequest(
            request.user_text,
            request.locale,
            request.context,
            request.output_schema,
            request.instructions,
            history=(
                ModelHistoryMessage("user", "Мы говорили о PDF по математике"),
                ModelHistoryMessage("assistant", "Да, помню тему разговора."),
            ),
        )
        with patch("ai_native_intents.ollama._open_loopback", side_effect=open_request):
            turn = OllamaModelProvider().route(request)

        self.assertEqual(turn.kind, ModelTurnKind.ACTION)
        self.assertEqual(turn.response_text, "Понял, подготовлю поиск.")
        self.assertEqual(
            [message["role"] for message in captured[0]["messages"]],
            ["system", "user"],
        )
        prompt = captured[0]["messages"][-1]["content"]
        self.assertNotIn("PDF по математике", prompt)
        self.assertIn("Do not copy arguments from earlier turns", prompt)
        self.assertEqual(captured[0]["options"]["num_ctx"], 8192)

    def test_reports_unavailable_system_action_without_executing_it(self):
        response = FakeResponse(
            {
                "message": {
                    "content": "Я понял, что нужна новая папка.",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "request_system_action",
                                "arguments": {
                                    "goal": "создать папку на рабочем столе",
                                    "confidence": 0.94,
                                },
                            }
                        }
                    ],
                }
            }
        )
        with patch("ai_native_intents.ollama._open_loopback", return_value=response):
            turn = OllamaModelProvider().route(
                model_request("Создай папку на рабочем столе")
            )

        self.assertEqual(turn.kind, ModelTurnKind.UNSUPPORTED_ACTION)
        self.assertIsNone(turn.intent_payload)
        self.assertEqual(
            turn.unsupported_actions,
            ("создать папку на рабочем столе",),
        )

    def test_removes_thinking_prefix_and_rejects_control_only_output(self):
        provider = OllamaModelProvider()
        self.assertEqual(provider._assistant_text("/no_think Привет"), "Привет")
        response = FakeResponse({"message": {"content": "<bool>false</bool>"}})
        with patch("ai_native_intents.ollama._open_loopback", return_value=response):
            with self.assertRaises(OllamaProviderError):
                provider.route(model_request("Привет"))

    def test_health_verifies_version_and_exact_model(self):
        responses = [
            FakeResponse({"version": "0.12.6"}),
            FakeResponse({"models": [{"name": "qwen3.5:2b", "model": "qwen3.5:2b"}]}),
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
                                "arguments": {
                                    "destination": "desktop",
                                    "confidence": 0.85,
                                },
                            }
                        },
                    ]
                }
            }
        )
        with patch("ai_native_intents.ollama._open_loopback", return_value=response):
            from dataclasses import replace

            result = OllamaModelProvider().compile(
                replace(
                    model_request(), user_text="Найди PDF и скопируй их на рабочий стол"
                )
            )

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
        with patch(
            "ai_native_intents.ollama._open_loopback", side_effect=socket.timeout()
        ):
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
