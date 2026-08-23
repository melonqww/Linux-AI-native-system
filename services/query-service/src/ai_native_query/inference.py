"""Public inference readiness composition shared by Workspace and panel runtime."""

from __future__ import annotations

from collections.abc import Callable


def workspace_model_readiness(
    provider_source: Callable[[], dict[str, object]] | None,
    model_source: Callable[[], dict[str, object]] | None,
) -> dict[str, object]:
    if provider_source is None:
        return {
            "state": "unavailable",
            "component": "provider.ollama",
            "reason": "provider_status_unavailable",
        }
    try:
        provider = provider_source()
    except Exception:
        return {
            "state": "unavailable",
            "component": "provider.ollama",
            "reason": "provider_status_unavailable",
        }
    if not isinstance(provider, dict):
        return {
            "state": "unavailable",
            "component": "provider.ollama",
            "reason": "provider_status_unavailable",
        }
    provider_state = provider.get("state")
    if provider_state != "ready":
        preparing = provider_state in {"starting", "downloading", "installing"}
        reason = provider.get("reason")
        return {
            "state": "starting" if preparing else "unavailable",
            "component": "provider.ollama",
            "provider_state": provider_state if isinstance(provider_state, str) else "error",
            "reason": reason if isinstance(reason, str) and reason else (
                "provider_preparing" if preparing else "ollama_provider_unavailable"
            ),
        }
    if model_source is None:
        return {
            "state": "unavailable",
            "component": "model.ollama",
            "reason": "model_manager_unavailable",
        }
    try:
        model = model_source()
    except Exception:
        return {
            "state": "unavailable",
            "component": "model.ollama",
            "reason": "model_manager_unavailable",
        }
    if not isinstance(model, dict):
        return {
            "state": "unavailable",
            "component": "model.ollama",
            "reason": "model_manager_unavailable",
        }
    return model
