import io
import json
import math
import unittest

from ai_native_turns import (
    CapabilityCandidateRouter,
    CapabilityDescriptor,
    EmbeddingUnavailableError,
    HybridCapabilityRouter,
    OllamaEmbeddingProvider,
    SemanticCapabilitySelector,
)


DESCRIPTORS = (
    CapabilityDescriptor(
        "documents.query.search",
        "search_documents",
        "Find local documents and their indexed content.",
        ("найди мои документы", "find my files"),
    ),
    CapabilityDescriptor(
        "storage.materialize.plan-copy",
        "copy_results",
        "Copy previously found files to a user destination.",
        ("скопируй найденные файлы", "copy the results"),
    ),
)


class FakeEmbeddingProvider:
    def __init__(self):
        self.calls = []
        self.fail = False

    def embed(self, texts):
        self.calls.append(tuple(texts))
        if self.fail:
            raise EmbeddingUnavailableError("offline")
        if len(texts) == 2:
            return ((1.0, 0.0), (0.0, 1.0))
        if "copiar" in texts[0]:
            return ((0.0, 1.0),)
        return ((1.0, 0.0),)


class FakeResponse:
    def __init__(self, payload):
        self.body = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.body.read(size)


class SemanticSelectorTests(unittest.TestCase):
    def test_multilingual_semantic_match_and_bounded_caches(self):
        provider = FakeEmbeddingProvider()
        selector = SemanticCapabilitySelector(DESCRIPTORS, provider)

        first = selector.candidates("por favor copiar los resultados")
        repeated = selector.candidates("por favor copiar los resultados")

        self.assertEqual([item.operation for item in first], ["copy_results"])
        self.assertEqual(repeated, first)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(len(provider.calls[0]), 2)

    def test_hybrid_falls_back_to_lexical_and_circuit_breaks_failures(self):
        provider = FakeEmbeddingProvider()
        provider.fail = True
        clock = [10.0]
        semantic = SemanticCapabilitySelector(
            DESCRIPTORS,
            provider,
            monotonic_fn=lambda: clock[0],
            unavailable_retry_seconds=60,
        )
        hybrid = HybridCapabilityRouter(
            CapabilityCandidateRouter(DESCRIPTORS), semantic
        )

        first = hybrid.candidates("найди мои документы")
        second = hybrid.candidates("найди мои документы")

        self.assertEqual(first[0].operation, "search_documents")
        self.assertEqual(second[0].operation, "search_documents")
        self.assertEqual(len(provider.calls), 1)

    def test_semantic_evidence_enables_classifier_without_authorizing_execution(self):
        provider = FakeEmbeddingProvider()
        hybrid = HybridCapabilityRouter(
            CapabilityCandidateRouter(DESCRIPTORS),
            SemanticCapabilitySelector(DESCRIPTORS, provider),
        )

        self.assertEqual(
            hybrid.requested_operations("por favor copiar los resultados"),
            ("copy_results",),
        )
        self.assertEqual(
            hybrid.available_operations(),
            ("search_documents", "copy_results"),
        )
        self.assertEqual(hybrid.required_operations("por favor copiar los resultados"), ())

    def test_rejects_remote_origins_and_malformed_vectors(self):
        with self.assertRaises(ValueError):
            OllamaEmbeddingProvider(base_url="https://example.com:11434")

        provider = OllamaEmbeddingProvider(
            open_fn=lambda *_args, **_kwargs: FakeResponse(
                {"model": "qwen3-embedding:0.6b", "embeddings": [[math.nan]]}
            )
        )
        with self.assertRaises(EmbeddingUnavailableError):
            provider.embed(("hello",))

    def test_ollama_adapter_uses_bounded_batch_endpoint(self):
        captured = []

        def open_request(request, **kwargs):
            captured.append((request, kwargs))
            return FakeResponse(
                {
                    "model": "qwen3-embedding:0.6b",
                    "embeddings": [[0.25, 0.75], [0.75, 0.25]],
                    "prompt_eval_count": 7,
                }
            )

        provider = OllamaEmbeddingProvider(open_fn=open_request)
        vectors = provider.embed(("один", "two"))

        body = json.loads(captured[0][0].data)
        self.assertEqual(captured[0][0].full_url, "http://127.0.0.1:11434/api/embed")
        self.assertEqual(body["model"], "qwen3-embedding:0.6b")
        self.assertEqual(body["input"], ["один", "two"])
        self.assertEqual(vectors, ((0.25, 0.75), (0.75, 0.25)))


if __name__ == "__main__":
    unittest.main()
