import io
import json
import shutil
import threading
import unittest
from pathlib import Path
from uuid import uuid4

from ai_native_model_ollama import OllamaModelManager


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Response:
    def __init__(self, payload=None, *, events=()):
        self.body = io.BytesIO(
            b"" if payload is None else json.dumps(payload).encode("utf-8")
        )
        self.events = iter(
            json.dumps(event).encode("utf-8") + b"\n" for event in events
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.body.read(size)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.events)


class ScriptedOllama:
    def __init__(self, *, present=False):
        self.present = present
        self.pull_started = threading.Event()

    def open(self, request, timeout):
        if request.full_url.endswith("/api/version"):
            return Response({"version": "0.12.6"})
        if request.full_url.endswith("/api/tags"):
            models = [{"name": "qwen3.5:2b"}] if self.present else []
            return Response({"models": models})
        if request.full_url.endswith("/api/pull"):
            body = json.loads(request.data)
            if body != {"model": "qwen3.5:2b", "stream": True}:
                raise AssertionError(body)
            self.pull_started.set()
            self.present = True
            return Response(
                events=(
                    {"status": "pulling", "completed": 50, "total": 100},
                    {"status": "success", "completed": 100, "total": 100},
                )
            )
        raise AssertionError(request.full_url)


class ModelManagerTests(unittest.TestCase):
    def setUp(self):
        self.root = PROJECT_ROOT / "tmp" / "model-manager-tests" / str(uuid4())
        self.root.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def wait_state(manager, expected):
        for _ in range(200):
            status = manager.status()
            if status.state == expected:
                return status
            threading.Event().wait(0.01)
        raise AssertionError(f"model manager did not reach {expected}")

    def test_existing_model_is_ready_without_download(self):
        ollama = ScriptedOllama(present=True)
        manager = OllamaModelManager(open_fn=ollama.open)

        status = manager.ensure()

        self.assertEqual(status.state, "ready")
        self.assertFalse(ollama.pull_started.is_set())

    def test_missing_model_downloads_in_background_and_reports_ready(self):
        ollama = ScriptedOllama(present=False)
        manager = OllamaModelManager(open_fn=ollama.open)
        try:
            initial = manager.ensure()
            ready = self.wait_state(manager, "ready")
        finally:
            manager.stop()

        self.assertEqual(initial.state, "starting")
        self.assertTrue(ollama.pull_started.is_set())
        self.assertEqual(ready.model, "qwen3.5:2b")
        self.assertTrue(ready.auto_download)

    def test_absent_ollama_is_a_stable_public_state(self):
        def unavailable(_request, timeout):
            raise OSError("private connection detail")

        manager = OllamaModelManager(
            open_fn=unavailable,
            executable_finder=lambda _name: None,
        )
        try:
            manager.ensure()
            status = self.wait_state(manager, "unavailable")
        finally:
            manager.stop()

        self.assertEqual(status.reason, "ollama_not_installed")
        self.assertNotIn("private", repr(status))

    def test_rejects_remote_endpoint_and_unbounded_model_name(self):
        for url in ("https://127.0.0.1:11434", "http://example.com:11434"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                OllamaModelManager(base_url=url)
        with self.assertRaises(ValueError):
            OllamaModelManager(model="../../bad")


if __name__ == "__main__":
    unittest.main()
