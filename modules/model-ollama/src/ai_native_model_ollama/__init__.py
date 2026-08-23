"""First-party Ollama model lifecycle worker."""

from __future__ import annotations

import os
from pathlib import Path

from .catalog import ModelDecisionStore, ModelDefinition, OllamaModelCatalog
from .contracts import ModelStatus
from .manager import OllamaModelManager


_catalog: OllamaModelCatalog | None = None


def worker_start() -> None:
    global _catalog
    database = Path(
        os.environ.get(
            "AI_NATIVE_MODEL_STATE_DATABASE",
            str(Path.home() / ".local/share/ai-native-linux/model-lifecycle.sqlite3"),
        )
    )
    definitions = (
        ModelDefinition(
            "workspace.qwen",
            os.environ.get("AI_NATIVE_INTENT_MODEL", "qwen3.5:2b"),
            "Qwen 3.5 2B",
            "workspace_base",
            True,
            2_700_000_000,
        ),
        ModelDefinition(
            "assistant.llama",
            os.environ.get("AI_NATIVE_LLAMA_MODEL", "llama3.2:3b"),
            "Llama 3.2 3B",
            "optional_assistant",
            False,
            2_000_000_000,
        ),
    )
    _catalog = OllamaModelCatalog(
        definitions,
        ModelDecisionStore(database),
        base_url=os.environ.get("AI_NATIVE_OLLAMA_URL", "http://127.0.0.1:11434"),
    )


def worker_health() -> dict[str, object]:
    return {"status": "ready", "model_count": len(_require_catalog().definitions)}


def worker_invoke(operation: str, payload: dict[str, object]) -> dict[str, object]:
    catalog = _require_catalog()
    if operation == "ensure":
        if payload:
            raise ValueError("invalid_payload")
        return catalog.ensure_base()
    if operation == "status":
        if payload:
            raise ValueError("invalid_payload")
        return catalog.ensure_base()
    if operation == "catalog":
        if payload:
            raise ValueError("invalid_payload")
        return catalog.catalog()
    if operation == "respond":
        if set(payload) != {"model_id", "decision"}:
            raise ValueError("invalid_payload")
        model_id = payload.get("model_id")
        decision = payload.get("decision")
        if not isinstance(model_id, str) or not isinstance(decision, str):
            raise ValueError("invalid_payload")
        return catalog.respond(model_id, decision)
    raise ValueError("unknown_operation")


def worker_stop() -> None:
    global _catalog
    if _catalog is not None:
        _catalog.stop()
    _catalog = None


def _require_catalog() -> OllamaModelCatalog:
    if _catalog is None:
        raise RuntimeError("model_manager_not_started")
    return _catalog


__all__ = [
    "ModelDecisionStore",
    "ModelDefinition",
    "ModelStatus",
    "OllamaModelCatalog",
    "OllamaModelManager",
]
