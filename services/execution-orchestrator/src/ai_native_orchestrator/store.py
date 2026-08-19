"""Bounded, expiring storage for plans created by the trusted compiler."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from time import monotonic

from ai_native_intents import ExecutionPlan


class PlanStoreError(ValueError):
    pass


@dataclass(frozen=True)
class _StoredPlan:
    plan: ExecutionPlan
    expires_at: float


class CompiledPlanStore:
    def __init__(self, *, capacity: int = 100, ttl_seconds: float = 900) -> None:
        if capacity < 1 or ttl_seconds <= 0:
            raise ValueError("plan store limits must be positive")
        self._capacity = capacity
        self._ttl_seconds = ttl_seconds
        self._lock = RLock()
        self._plans: OrderedDict[str, _StoredPlan] = OrderedDict()

    def put(self, plan: ExecutionPlan) -> None:
        with self._lock:
            self._purge()
            self._plans[plan.plan_id] = _StoredPlan(plan, monotonic() + self._ttl_seconds)
            self._plans.move_to_end(plan.plan_id)
            while len(self._plans) > self._capacity:
                self._plans.popitem(last=False)

    def claim(self, plan_id: str) -> ExecutionPlan:
        if not isinstance(plan_id, str) or not plan_id.strip() or len(plan_id) > 128:
            raise PlanStoreError("invalid_plan_id")
        with self._lock:
            self._purge()
            stored = self._plans.pop(plan_id, None)
        if stored is None:
            raise PlanStoreError("plan_not_found_or_already_claimed")
        return stored.plan

    def _purge(self) -> None:
        now = monotonic()
        expired = [key for key, value in self._plans.items() if value.expires_at <= now]
        for key in expired:
            self._plans.pop(key, None)
