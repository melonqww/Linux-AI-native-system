"""Loopback-only JSON bridge consumed by the desktop panel."""

from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol


class RuntimeApplication(Protocol):
    def capabilities(self) -> list[str]: ...
    def search(self, payload: dict[str, object]) -> list[object]: ...
    def index_status(self) -> dict[str, object]: ...
    def compile_intent(self, payload: dict[str, object]) -> object: ...


def create_server(application: RuntimeApplication, host: str = "127.0.0.1", port: int = 0):
    if host != "127.0.0.1":
        raise ValueError("panel bridge must bind to IPv4 loopback")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/v1/health":
                self._send(200, {"status": "ok"})
            elif self.path == "/v1/capabilities":
                self._send(200, {"capabilities": application.capabilities()})
            elif self.path == "/v1/index-status":
                self._send(200, application.index_status())
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:
            try:
                content_type = self.headers.get_content_type()
                if content_type != "application/json":
                    raise ValueError("content_type_must_be_application_json")
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    raise ValueError("request_body_required")
                if length > 64 * 1024:
                    raise ValueError("request_too_large")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("body_must_be_object")
                if self.path == "/v1/search":
                    self._send(200, {"results": [asdict(item) for item in application.search(payload)]})
                elif self.path == "/v1/intent/compile":
                    result = application.compile_intent(payload)
                    self._send(200, asdict(result))
                else:
                    self._send(404, {"error": "not_found"})
            except RuntimeError as error:
                self._send(503, {"error": str(error)})
            except (ValueError, json.JSONDecodeError) as error:
                self._send(400, {"error": str(error)})

        def log_message(self, _format: str, *args: object) -> None:
            return

        def _send(self, status: int, payload: object) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
