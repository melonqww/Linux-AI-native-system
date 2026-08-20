"""First-party system.updates worker entrypoint."""

from __future__ import annotations

from .checker import UbuntuUpdateChecker
from .contracts import UpdateCheck


_checker: UbuntuUpdateChecker | None = None


def worker_start() -> None:
    global _checker
    _checker = UbuntuUpdateChecker()


def worker_health() -> dict[str, object]:
    checker = _require_checker()
    return {
        "status": "ready",
        "platform_supported": checker.platform.startswith("linux"),
    }


def worker_invoke(operation: str, payload: dict[str, object]) -> dict[str, object]:
    if operation != "check":
        raise ValueError("unknown_operation")
    if payload:
        raise ValueError("invalid_payload")
    return _require_checker().check().to_dict()


def worker_stop() -> None:
    global _checker
    _checker = None


def _require_checker() -> UbuntuUpdateChecker:
    if _checker is None:
        raise RuntimeError("checker_not_started")
    return _checker


__all__ = ["UbuntuUpdateChecker", "UpdateCheck"]
