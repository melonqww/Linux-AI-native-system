"""First-party Security Center foundation worker.

Importing this package only defines contracts and lifecycle functions. It does
not perform I/O, allocate external resources, or start the worker.
"""

from __future__ import annotations

from .contracts import SecurityModuleStatus


_started = False


def worker_start() -> None:
    """Mark the dependency-free worker ready; repeated starts are harmless."""

    global _started
    _started = True


def worker_health() -> dict[str, object]:
    """Return bounded health details or fail closed while stopped."""

    _require_started()
    return {"status": "ready"}


def worker_invoke(operation: str, payload: dict[str, object]) -> dict[str, object]:
    """Invoke the sole foundation operation using its closed input contract."""

    if operation != "status":
        raise ValueError("unknown_operation")
    if type(payload) is not dict or payload:
        raise ValueError("invalid_payload")
    _require_started()
    return SecurityModuleStatus().to_dict()


def worker_stop() -> None:
    """Stop the worker; repeated stops are harmless."""

    global _started
    _started = False


def _require_started() -> None:
    if not _started:
        raise RuntimeError("security_worker_not_started")


__all__ = [
    "SecurityModuleStatus",
    "worker_health",
    "worker_invoke",
    "worker_start",
    "worker_stop",
]
