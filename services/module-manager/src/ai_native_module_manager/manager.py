"""Dependency-aware process lifecycle for enabled capability modules."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from time import monotonic

from ai_native_capabilities import CapabilityRegistry, ModuleState


class ModuleProcessError(RuntimeError):
    pass


@dataclass
class _RunningModule:
    process: subprocess.Popen[str]
    last_used: float


class ModuleProcessManager:
    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        idle_seconds: float = 300.0,
        response_timeout: float = 5.0,
    ) -> None:
        if idle_seconds < 0:
            raise ValueError("idle_seconds must not be negative")
        if response_timeout <= 0:
            raise ValueError("response_timeout must be positive")
        self.registry = registry
        self.idle_seconds = idle_seconds
        self.response_timeout = response_timeout
        self._running: dict[str, _RunningModule] = {}

    def start_for_capability(self, capability_id: str) -> str:
        providers = self.registry.providers(capability_id)
        if not providers:
            raise ModuleProcessError(f"no enabled provider for capability: {capability_id}")
        module_id = providers[0].module_id
        self.start_module(module_id)
        return module_id

    def start_module(self, module_id: str) -> None:
        running = self._running.get(module_id)
        if running and running.process.poll() is None:
            running.last_used = monotonic()
            return
        module = self.registry.get_module(module_id)
        if module.state is not ModuleState.ENABLED:
            raise ModuleProcessError(f"module {module_id} is {module.state.value}: {module.state_reason}")
        for dependency in module.manifest.dependencies:
            self.start_module(dependency)

        module_root = Path(module.manifest_path).parent
        python_path = (module_root / module.manifest.entrypoint.python_path).resolve(strict=True)
        manager_src = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        path_parts = [str(python_path), str(manager_src)]
        if env.get("PYTHONPATH"):
            path_parts.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(path_parts)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ai_native_module_manager.worker",
                module.manifest.entrypoint.module,
            ],
            cwd=module_root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        assert process.stdout is not None
        try:
            response = self._read_response(process, self.response_timeout)
        except ModuleProcessError:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
            self._close_streams(process)
            self.registry.quarantine(module_id, "worker_start_failed")
            raise
        if response.get("event") != "ready":
            process.kill()
            process.wait(timeout=2)
            self._close_streams(process)
            error = response.get("error", "worker did not become ready")
            self.registry.quarantine(module_id, str(error))
            raise ModuleProcessError(f"module {module_id} failed to start: {error}")
        self._running[module_id] = _RunningModule(process=process, last_used=monotonic())

    def health(self, module_id: str) -> bool:
        running = self._require_running(module_id)
        response = self._request(running.process, {"command": "health"})
        running.last_used = monotonic()
        return response.get("event") == "healthy"

    def stop_module(self, module_id: str) -> None:
        running = self._running.pop(module_id, None)
        if running is None:
            return
        process = running.process
        if process.poll() is None:
            try:
                self._request(process, {"command": "shutdown"})
                process.wait(timeout=2)
            except (ModuleProcessError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=2)
        self._close_streams(process)

    def reap_idle(self, *, now: float | None = None) -> list[str]:
        current = monotonic() if now is None else now
        stopped: list[str] = []
        for module_id, running in list(self._running.items()):
            module = self.registry.get_module(module_id)
            if module.manifest.lifecycle != "on-demand":
                continue
            if current - running.last_used >= self.idle_seconds:
                self.stop_module(module_id)
                stopped.append(module_id)
        return stopped

    def running_modules(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                module_id
                for module_id, running in self._running.items()
                if running.process.poll() is None
            )
        )

    def stop_all(self) -> None:
        for module_id in list(self._running):
            self.stop_module(module_id)

    def _require_running(self, module_id: str) -> _RunningModule:
        running = self._running.get(module_id)
        if running is None or running.process.poll() is not None:
            raise ModuleProcessError(f"module is not running: {module_id}")
        return running

    def _request(self, process: subprocess.Popen[str], payload: dict[str, object]) -> dict[str, object]:
        if process.stdin is None:
            raise ModuleProcessError("worker stdin is closed")
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        return self._read_response(process, self.response_timeout)

    @staticmethod
    def _read_response(process: subprocess.Popen[str], timeout: float) -> dict[str, object]:
        if process.stdout is None:
            raise ModuleProcessError("worker stdout is closed")
        queue: Queue[str] = Queue(maxsize=1)
        threading.Thread(
            target=lambda: queue.put(process.stdout.readline()),
            daemon=True,
        ).start()
        try:
            line = queue.get(timeout=timeout)
        except Empty as error:
            raise ModuleProcessError("worker response timed out") from error
        if not line:
            stderr = process.stderr.read() if process.stderr else ""
            raise ModuleProcessError(f"worker exited without response: {stderr.strip()}")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise ModuleProcessError("worker returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise ModuleProcessError("worker response must be an object")
        return payload

    @staticmethod
    def _close_streams(process: subprocess.Popen[str]) -> None:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
