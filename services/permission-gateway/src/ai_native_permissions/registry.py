"""Bounded dispatcher for handlers selected only by trusted capability IDs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from threading import BoundedSemaphore, RLock
from time import monotonic

from .contracts import CapabilityInvocation, DispatchResult, PermissionDecision
from .gateway import PermissionGateway, PolicyConfigurationError


CapabilityHandler = Callable[[CapabilityInvocation], object]


class HandlerRegistrationError(ValueError):
    pass


class CapabilityBusyError(RuntimeError):
    pass


class CapabilityExecutionRegistry:
    def __init__(self, gateway: PermissionGateway) -> None:
        self.gateway = gateway
        self._lock = RLock()
        self._handlers: dict[str, CapabilityHandler] = {}
        self._limits: dict[str, BoundedSemaphore] = {}

    def register(self, capability_id: str, handler: CapabilityHandler) -> None:
        if not callable(handler):
            raise HandlerRegistrationError("capability handler must be callable")
        try:
            policy = self.gateway.policy(capability_id)
        except PolicyConfigurationError as error:
            raise HandlerRegistrationError("handler has no trusted policy") from error
        with self._lock:
            if capability_id in self._handlers:
                raise HandlerRegistrationError("duplicate capability handler")
            self._handlers[capability_id] = handler
            self._limits[capability_id] = BoundedSemaphore(policy.max_concurrency)

    def dispatch(
        self,
        invocation: CapabilityInvocation,
        *,
        decision_observer: Callable[[PermissionDecision], None] | None = None,
    ) -> DispatchResult:
        decision = self.gateway.evaluate(invocation)
        if decision_observer is not None:
            decision_observer(decision)
        if not decision.allowed:
            return DispatchResult(decision)
        with self._lock:
            handler = self._handlers.get(invocation.capability_id)
            limit = self._limits.get(invocation.capability_id)
        if handler is None or limit is None:
            return DispatchResult(
                self.gateway.denied(
                    invocation,
                    self.gateway.policy(invocation.capability_id),
                    "handler_unavailable",
                )
            )
        if not limit.acquire(blocking=False):
            raise CapabilityBusyError("capability concurrency limit reached")
        try:
            policy = self.gateway.policy(invocation.capability_id)
            bounded = replace(
                invocation, deadline_monotonic=monotonic() + policy.timeout_seconds
            )
            return DispatchResult(decision, handler(bounded))
        finally:
            limit.release()

    def registered_capabilities(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._handlers))
