from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .contracts import SoftwareTask
from .manager import SoftwareManager


class StopSignal(Protocol):
    def wait(self, timeout: float) -> bool: ...


class RecoveryWorker:
    """Small polling worker intended for the future user-session service."""

    def __init__(
        self,
        manager: SoftwareManager,
        *,
        interval_seconds: float = 2.0,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if interval_seconds < 0.5 or interval_seconds > 60:
            raise ValueError("recovery interval must be between 0.5 and 60 seconds")
        self.manager = manager
        self.interval_seconds = interval_seconds
        self._on_error = on_error

    def run_once(self) -> tuple[SoftwareTask, ...]:
        return self.manager.reconcile()

    def run_forever(self, stop_signal: StopSignal) -> None:
        while True:
            try:
                self.run_once()
            except Exception as error:
                if self._on_error is not None:
                    self._on_error(error)
            if stop_signal.wait(self.interval_seconds):
                return
