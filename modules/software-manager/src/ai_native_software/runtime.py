from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .contracts import ApplicationRuntime, RuntimeStatus


_DESKTOP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}\.desktop$")


class DesktopRuntimeAdapter(Protocol):
    def launch(self, desktop_id: str) -> None: ...

    def status(self, desktop_id: str) -> RuntimeStatus: ...

    def close(self, desktop_id: str) -> None: ...

    def set_pinned(self, desktop_id: str, pinned: bool) -> bool: ...


class ApplicationLifecycleManager:
    def __init__(
        self,
        adapter: DesktopRuntimeAdapter,
        *,
        now_fn: Callable[[], datetime] | None = None,
        launch_timeout: timedelta = timedelta(seconds=15),
    ) -> None:
        if launch_timeout <= timedelta(0):
            raise ValueError("launch_timeout must be positive")
        self.adapter = adapter
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self.launch_timeout = launch_timeout
        self._states: dict[str, ApplicationRuntime] = {}

    def observe(self, application_id: str, desktop_id: str) -> ApplicationRuntime:
        self._validate_desktop_id(desktop_id)
        current = self._states.get(application_id)
        if current is None:
            current = ApplicationRuntime(application_id, desktop_id, "stopped")
        status = self.adapter.status(desktop_id)
        return self._remember(self._merge_observation(current, status))

    def launch(self, application_id: str, desktop_id: str) -> ApplicationRuntime:
        self._validate_desktop_id(desktop_id)
        current = self.observe(application_id, desktop_id)
        if not current.can_launch:
            raise ValueError("application_cannot_launch")
        try:
            self.adapter.launch(desktop_id)
        except Exception:
            return self._remember(replace(current, state="launch_failed", requested_at=None))
        return self._remember(
            replace(current, state="starting", requested_at=self._now().isoformat())
        )

    def retry(self, application_id: str) -> ApplicationRuntime:
        current = self._get(application_id)
        if not current.can_retry:
            raise ValueError("application_cannot_retry")
        return self.launch(current.application_id, current.desktop_id)

    def close(self, application_id: str) -> ApplicationRuntime:
        current = self.refresh(application_id)
        if not current.can_close:
            raise ValueError("application_cannot_close")
        try:
            self.adapter.close(current.desktop_id)
        except Exception:
            return current
        return self._remember(
            replace(current, state="stopping", requested_at=self._now().isoformat())
        )

    def refresh(self, application_id: str) -> ApplicationRuntime:
        current = self._get(application_id)
        status = self.adapter.status(current.desktop_id)
        return self._remember(self._merge_observation(current, status))

    def set_pinned(self, application_id: str, pinned: bool) -> bool:
        current = self._get(application_id)
        return self.adapter.set_pinned(current.desktop_id, pinned)

    def _merge_observation(
        self,
        current: ApplicationRuntime,
        status: RuntimeStatus,
    ) -> ApplicationRuntime:
        if status.state == "running":
            if current.state == "stopping" and not self._launch_timed_out(current):
                return current
            state = "running" if status.window_count > 0 else "running_in_background"
            return replace(current, state=state, requested_at=None)
        if status.state == "starting":
            return replace(current, state="starting")
        if status.state == "stopped":
            if current.state == "starting" and not self._launch_timed_out(current):
                return current
            state = "launch_failed" if current.state == "starting" else "stopped"
            return replace(current, state=state, requested_at=None)
        if current.state == "starting" and self._launch_timed_out(current):
            return replace(current, state="launch_failed", requested_at=None)
        return replace(current, state="state_unknown")

    def _launch_timed_out(self, current: ApplicationRuntime) -> bool:
        if current.requested_at is None:
            return False
        requested = datetime.fromisoformat(current.requested_at).astimezone(UTC)
        return self._now() - requested >= self.launch_timeout

    def _get(self, application_id: str) -> ApplicationRuntime:
        try:
            return self._states[application_id]
        except KeyError as error:
            raise KeyError(f"runtime_not_observed:{application_id}") from error

    def _remember(self, state: ApplicationRuntime) -> ApplicationRuntime:
        self._states[state.application_id] = state
        return state

    def _now(self) -> datetime:
        value = self._now_fn()
        if value.tzinfo is None:
            raise ValueError("clock must return timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _validate_desktop_id(desktop_id: str) -> None:
        if not isinstance(desktop_id, str) or _DESKTOP_ID.fullmatch(desktop_id) is None:
            raise ValueError("invalid_desktop_id")
