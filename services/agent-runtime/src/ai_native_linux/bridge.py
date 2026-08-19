"""Loopback-only JSON bridge consumed by the desktop panel."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .routing import RuntimeApplication, RuntimeRouter


def create_server(application: RuntimeApplication, host: str = "127.0.0.1", port: int = 0):
    if host != "127.0.0.1":
        raise ValueError("panel bridge must bind to IPv4 loopback")
    # TCP loopback is a development fallback. It can inspect and execute R0,
    # but it must never confirm a filesystem-changing R1 operation.
    router = RuntimeRouter(application, allow_r1=False)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            response = router.dispatch("GET", self.path)
            self._send(response.status, response.payload)

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
                response = router.dispatch("POST", self.path, payload)
                self._send(response.status, response.payload)
            except (ValueError, json.JSONDecodeError):
                response = router.error(400, "invalid_request", False)
                self._send(response.status, response.payload)
            except Exception:
                response = router.error(500, "internal_error", True)
                self._send(response.status, response.payload)

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
