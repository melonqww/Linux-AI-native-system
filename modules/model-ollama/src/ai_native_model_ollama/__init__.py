"""First-party Ollama model lifecycle worker."""

from __future__ import annotations

import os

from .contracts import ModelStatus
from .manager import OllamaModelManager


_manager: OllamaModelManager | None = None


def worker_start() -> None:
    global _manager
    _manager = OllamaModelManager(
        model=os.environ.get("AI_NATIVE_INTENT_MODEL", "qwen3:1.7b"),
        base_url=os.environ.get("AI_NATIVE_OLLAMA_URL", "http://127.0.0.1:11434"),
    )


def worker_health() -> dict[str, object]:
    return _require_manager().status().to_dict()


def worker_invoke(operation: str, payload: dict[str, object]) -> dict[str, object]:
    if payload:
        raise ValueError("invalid_payload")
    manager = _require_manager()
    if operation == "ensure":
        return manager.ensure().to_dict()
    if operation == "status":
        return manager.status().to_dict()
    raise ValueError("unknown_operation")


def worker_stop() -> None:
    global _manager
    if _manager is not None:
        _manager.stop()
    _manager = None


def _require_manager() -> OllamaModelManager:
    if _manager is None:
        raise RuntimeError("model_manager_not_started")
    return _manager


__all__ = ["ModelStatus", "OllamaModelManager"]
