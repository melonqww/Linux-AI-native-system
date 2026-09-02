"""Deterministic lab-only fault injection around production boundaries."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable
from typing import TypeVar

from .contracts import FaultEffect, FaultSpec


T = TypeVar("T")


class InjectedLabFailure(RuntimeError):
    pass


class FaultController:
    ALLOWED_POINTS = frozenset(
        {
            "model.classify_turn",
            "model.respond_chat",
            "model.route",
            "model.summarize_result",
            "model.compose_conversation",
            "executor.execute",
            "executor.approval_response",
        }
    )

    def __init__(self, faults: tuple[FaultSpec, ...] = ()) -> None:
        self._faults = faults
        self._calls: dict[str, int] = defaultdict(int)
        self.events: list[dict[str, object]] = []

    def call(self, point: str, callback: Callable[[], T]) -> T:
        if point not in self.ALLOWED_POINTS:
            raise ValueError(f"unsupported fault point: {point}")
        self._calls[point] += 1
        occurrence = self._calls[point]
        fault = next(
            (
                item
                for item in self._faults
                if item.point == point and item.occurrence == occurrence
            ),
            None,
        )
        if fault is None:
            return callback()
        self.events.append(
            {
                "point": point,
                "occurrence": occurrence,
                "effect": fault.effect.value,
                "delay_ms": fault.delay_ms,
            }
        )
        if fault.effect is FaultEffect.DELAY:
            time.sleep(fault.delay_ms / 1_000)
            return callback()
        if fault.effect is FaultEffect.TIMEOUT:
            raise TimeoutError(f"injected_timeout:{point}")
        if fault.effect is FaultEffect.RAISE:
            raise InjectedLabFailure(f"injected_failure:{point}")
        if point != "model.classify_turn":
            raise ValueError("malformed injection is allowed only for classification")
        return {}  # type: ignore[return-value]
