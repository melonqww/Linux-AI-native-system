"""First-party user-local Ollama provider worker."""

from __future__ import annotations

import os
from pathlib import Path

from .contracts import ProviderStatus
from .installer import OllamaProviderInstaller
from .store import ProviderDecisionStore


_installer: OllamaProviderInstaller | None = None


def worker_start() -> None:
    global _installer
    root = Path(os.environ["AI_NATIVE_OLLAMA_PROVIDER_ROOT"])
    database = Path(os.environ["AI_NATIVE_PROVIDER_STATE_DATABASE"])
    _installer = OllamaProviderInstaller(
        root,
        ProviderDecisionStore(database),
        base_url=os.environ.get("AI_NATIVE_OLLAMA_URL", "http://127.0.0.1:11434"),
    )


def worker_health() -> dict[str, object]:
    return {"status": "ready", "provider": "ollama"}


def worker_invoke(operation: str, payload: dict[str, object]) -> dict[str, object]:
    installer = _require_installer()
    if operation == "status":
        if payload:
            raise ValueError("invalid_payload")
        return installer.status().to_dict()
    if operation == "respond":
        if set(payload) != {"decision"} or not isinstance(payload.get("decision"), str):
            raise ValueError("invalid_payload")
        return installer.respond(payload["decision"]).to_dict()
    raise ValueError("unknown_operation")


def worker_stop() -> None:
    global _installer
    if _installer is not None:
        _installer.stop()
    _installer = None


def _require_installer() -> OllamaProviderInstaller:
    if _installer is None:
        raise RuntimeError("provider_installer_not_started")
    return _installer


__all__ = ["OllamaProviderInstaller", "ProviderDecisionStore", "ProviderStatus"]
