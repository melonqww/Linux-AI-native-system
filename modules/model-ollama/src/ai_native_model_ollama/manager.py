"""Bounded background lifecycle manager for a loopback Ollama model."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from .contracts import ModelStatus


_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}:[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
_MAX_JSON_BYTES = 1024 * 1024
_MAX_STREAM_LINE = 256 * 1024


class OllamaModelManager:
    def __init__(
        self,
        *,
        model: str = "qwen3.5:2b",
        base_url: str = "http://127.0.0.1:11434",
        open_fn: Callable[..., object] | None = None,
        executable_finder: Callable[[str], str | None] | None = None,
        popen_fn: Callable[..., subprocess.Popen[bytes]] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        lifecycle_lock: threading.Lock | threading.RLock | None = None,
    ) -> None:
        if not isinstance(model, str) or _MODEL.fullmatch(model) is None:
            raise ValueError("model name is invalid")
        self.model = model
        self.base_url, self.ollama_host = self._base_url(base_url)
        self._open_fn = open_fn or build_opener(ProxyHandler({})).open
        self._find = executable_finder or shutil.which
        self._popen = popen_fn or subprocess.Popen
        self._sleep = sleep_fn or time.sleep
        self._lifecycle_lock = lifecycle_lock or threading.RLock()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._owned_server: subprocess.Popen[bytes] | None = None
        self._status = self._make_status("checking")

    def ensure(self) -> ModelStatus:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._status
            if self._model_present():
                self._status = self._make_status("ready")
                return self._status
            self._stop.clear()
            self._status = self._make_status("starting")
            self._thread = threading.Thread(
                target=self._ensure_worker,
                name="ollama-model-download",
                daemon=True,
            )
            self._thread.start()
            return self._status

    def status(self) -> ModelStatus:
        with self._lock:
            if self._status.state in {"checking", "unavailable", "error"}:
                if self._model_present():
                    self._status = self._make_status("ready")
            return self._status

    def installed(self) -> bool:
        return self._model_present()

    def policy_status(self, state: str, *, reason: str | None = None) -> ModelStatus:
        """Build a status owned by catalog policy without starting model work."""
        return self._make_status(state, reason=reason, auto_download=False)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        process = self._owned_server
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        self._owned_server = None

    def _ensure_worker(self) -> None:
        with self._lifecycle_lock:
            self._ensure_worker_locked()

    def _ensure_worker_locked(self) -> None:
        try:
            if not self._server_available() and not self._start_server():
                return
            if self._model_present():
                self._set_status("ready")
                return
            self._set_status("downloading", completed=0, total=None)
            self._pull_model()
            if self._stop.is_set():
                self._set_status("unavailable", reason="download_interrupted")
            elif self._model_present():
                self._set_status("ready")
            else:
                self._set_status("error", reason="model_not_available_after_download")
        except Exception:
            self._set_status("error", reason="download_failed")

    def _start_server(self) -> bool:
        executable = self._find("ollama")
        if not executable:
            self._set_status("unavailable", reason="ollama_not_installed")
            return False
        try:
            resolved = Path(executable).expanduser().resolve(strict=True)
        except OSError:
            self._set_status("unavailable", reason="ollama_not_installed")
            return False
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            self._set_status("unavailable", reason="ollama_not_installed")
            return False
        environment = os.environ.copy()
        environment["OLLAMA_HOST"] = self.ollama_host
        try:
            self._owned_server = self._popen(
                [str(resolved), "serve"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
                cwd=Path.home(),
            )
        except OSError:
            self._set_status("unavailable", reason="ollama_start_failed")
            return False
        for _ in range(40):
            if self._stop.is_set():
                return False
            if self._server_available():
                return True
            if self._owned_server.poll() is not None:
                break
            self._sleep(0.25)
        self._set_status("unavailable", reason="ollama_start_failed")
        return False

    def _server_available(self) -> bool:
        try:
            payload = self._json_request("GET", "/api/version", timeout=2)
        except (OSError, RuntimeError, ValueError):
            return False
        return isinstance(payload.get("version"), str)

    def _model_present(self) -> bool:
        try:
            payload = self._json_request("GET", "/api/tags", timeout=2)
        except (OSError, RuntimeError, ValueError):
            return False
        models = payload.get("models")
        if not isinstance(models, list):
            return False
        names = {
            value
            for item in models
            if isinstance(item, Mapping)
            for value in (item.get("name"), item.get("model"))
            if isinstance(value, str)
        }
        return self.model in names

    def _pull_model(self) -> None:
        request = Request(
            f"{self.base_url}/api/pull",
            data=json.dumps({"model": self.model, "stream": True}).encode("utf-8"),
            headers={"Accept": "application/x-ndjson", "Content-Type": "application/json"},
            method="POST",
        )
        with self._open_fn(request, timeout=30) as response:
            for raw in response:
                if self._stop.is_set():
                    return
                if len(raw) > _MAX_STREAM_LINE:
                    raise RuntimeError("pull response line is too large")
                try:
                    event = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise RuntimeError("pull response is invalid") from error
                if not isinstance(event, Mapping):
                    raise RuntimeError("pull event must be an object")
                if event.get("error"):
                    raise RuntimeError("ollama pull failed")
                completed = self._count(event.get("completed"), default=0)
                total = self._count(event.get("total"), default=None)
                self._set_status("downloading", completed=completed, total=total)

    def _json_request(self, method: str, path: str, *, timeout: float) -> Mapping[str, object]:
        request = Request(
            f"{self.base_url}{path}", headers={"Accept": "application/json"}, method=method
        )
        with self._open_fn(request, timeout=timeout) as response:
            body = response.read(_MAX_JSON_BYTES + 1)
        if len(body) > _MAX_JSON_BYTES:
            raise RuntimeError("Ollama response is too large")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("Ollama response is invalid") from error
        if not isinstance(payload, Mapping):
            raise RuntimeError("Ollama response must be an object")
        return payload

    def _set_status(
        self,
        state: str,
        *,
        completed: int = 0,
        total: int | None = None,
        reason: str | None = None,
    ) -> None:
        with self._lock:
            self._status = self._make_status(
                state, completed=completed, total=total, reason=reason
            )

    def _make_status(
        self,
        state: str,
        *,
        completed: int = 0,
        total: int | None = None,
        reason: str | None = None,
        auto_download: bool = True,
    ) -> ModelStatus:
        progress = None
        if total is not None and total > 0:
            progress = min(100, int(completed * 100 / total))
        return ModelStatus(
            1,
            "ollama",
            self.model,
            state,
            progress,
            completed,
            total,
            reason,
            auto_download,
        )

    @staticmethod
    def _count(value: object, *, default: int | None) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return default
        return value

    @staticmethod
    def _base_url(value: str) -> tuple[str, str]:
        if not isinstance(value, str):
            raise TypeError("base_url must be a string")
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme != "http"
            or parsed.hostname not in _LOOPBACK
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Ollama base URL must be a loopback HTTP origin")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("Ollama base URL has an invalid port") from error
        if port is None:
            raise ValueError("Ollama base URL must include a port")
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        return f"http://{host}:{port}", f"{host}:{port}"
