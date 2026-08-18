"""Small resource gate used before each scheduler batch."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass


def normalized_load() -> float:
    try:
        one_minute = os.getloadavg()[0]
    except (AttributeError, OSError):
        return 0.0
    return one_minute / max(1, os.cpu_count() or 1)


@dataclass(frozen=True)
class ResourceBudget:
    max_batch: int = 16
    max_normalized_load: float = 1.25
    load_probe: Callable[[], float] = normalized_load

    def __post_init__(self) -> None:
        if self.max_batch < 1:
            raise ValueError("max_batch must be positive")
        if self.max_normalized_load <= 0:
            raise ValueError("max_normalized_load must be positive")

    def should_pause(self) -> bool:
        return self.load_probe() > self.max_normalized_load
