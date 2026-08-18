import json
import sys
import threading
import unittest
import urllib.request
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_native_linux.bridge import create_server


@dataclass
class Result:
    path: str


class App:
    def capabilities(self):
        return ["storage.catalog.search"]

    def search(self, payload):
        return [Result(path=f"result:{payload['text']}")]


class BridgeTests(unittest.TestCase):
    def test_rejects_non_loopback_binding(self):
        with self.assertRaises(ValueError):
            create_server(App(), host="0.0.0.0")

    def test_health_capabilities_and_search(self):
        server = create_server(App())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            health = json.load(urllib.request.urlopen(base + "/v1/health"))
            request = urllib.request.Request(
                base + "/v1/search",
                data=json.dumps({"text": "math"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            results = json.load(urllib.request.urlopen(request))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(health, {"status": "ok"})
        self.assertEqual(results["results"][0]["path"], "result:math")
