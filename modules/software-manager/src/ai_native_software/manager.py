from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from .catalog import get_application, list_applications
from .contracts import (
    Application,
    BackendProgress,
    InstallPreferences,
    SoftwareTask,
    TASK_ACTIONS,
    TERMINAL_STATES,
)
from .snapd import SnapdError
from .store import SoftwareTaskStore


class SoftwareBackend(Protocol):
    def start(self, action: str, package_name: str, *, channel: str = "stable") -> str: ...

    def progress(self, change_id: str) -> BackendProgress: ...

    def abort(self, change_id: str) -> None: ...


class SoftwareManager:
    def __init__(
        self,
        store: SoftwareTaskStore,
        backend: SoftwareBackend,
        *,
        now_fn: Callable[[], datetime] | None = None,
        id_fn: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.backend = backend
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._id_fn = id_fn or (lambda: uuid4().hex)

    def catalog(self) -> tuple[Application, ...]:
        return list_applications()

    def prepare_install(
        self,
        application_id: str,
        preferences: InstallPreferences | None = None,
    ) -> SoftwareTask:
        return self._prepare(application_id, "install", preferences)

    def prepare_remove(self, application_id: str) -> SoftwareTask:
        return self._prepare(application_id, "remove")

    def confirm(self, task_id: str) -> SoftwareTask:
        task = self.store.get(task_id)
        if task.state != "awaiting_confirmation":
            raise ValueError("task_not_awaiting_confirmation")
        return self._start(task)

    def pause(self, task_id: str) -> SoftwareTask:
        task = self.store.get(task_id)
        if not task.can_pause or not task.external_id:
            raise ValueError("task_cannot_pause")
        self.backend.abort(task.external_id)
        return self._save(
            task,
            state="paused",
            phase="paused",
            error_code=None,
        )

    def resume(self, task_id: str) -> SoftwareTask:
        task = self.store.get(task_id)
        if not task.can_resume:
            raise ValueError("task_cannot_resume")
        return self._start(task)

    def cancel(self, task_id: str) -> SoftwareTask:
        task = self.store.get(task_id)
        if not task.can_cancel:
            raise ValueError("task_cannot_cancel")
        if task.state == "running" and task.external_id:
            self.backend.abort(task.external_id)
        return self._save(task, state="canceled", phase="canceled", error_code=None)

    def refresh(self, task_id: str) -> SoftwareTask:
        task = self.store.get(task_id)
        if task.state != "running" or not task.external_id:
            return task
        try:
            progress = self.backend.progress(task.external_id)
        except SnapdError as error:
            return self._save(task, error_code=error.code)
        percent = progress.progress_percent
        if task.progress_percent is not None and percent is not None:
            percent = max(task.progress_percent, percent)
        return self._save(
            task,
            state=progress.state,
            phase=progress.phase,
            progress_percent=percent,
            error_code=progress.error_code,
        )

    def refresh_active(self) -> tuple[SoftwareTask, ...]:
        refreshed: list[SoftwareTask] = []
        for task in self.store.list():
            refreshed.append(self.refresh(task.task_id) if task.state == "running" else task)
        return tuple(refreshed)

    def get(self, task_id: str) -> SoftwareTask:
        return self.store.get(task_id)

    def list_tasks(self) -> tuple[SoftwareTask, ...]:
        return self.store.list()

    def _prepare(
        self,
        application_id: str,
        action: str,
        preferences: InstallPreferences | None = None,
    ) -> SoftwareTask:
        if action not in TASK_ACTIONS:
            raise ValueError("unsupported_action")
        application = get_application(application_id)
        selected_preferences = preferences or InstallPreferences()
        self._validate_preferences(application, selected_preferences)
        now = self._timestamp()
        task = SoftwareTask(
            schema_version=1,
            task_id=self._id_fn(),
            application_id=application.application_id,
            display_name=application.display_name,
            provider=application.provider,
            package_name=application.package_name,
            action=action,
            preferences=selected_preferences,
            state="awaiting_confirmation",
            phase="planning",
            progress_percent=None,
            external_id=None,
            created_at=now,
            updated_at=now,
        )
        return self.store.save(task)

    @staticmethod
    def _validate_preferences(
        application: Application,
        preferences: InstallPreferences,
    ) -> None:
        if preferences.locale not in application.supported_locales:
            raise ValueError("unsupported_application_locale")
        if preferences.install_location not in application.supported_install_locations:
            raise ValueError("unsupported_install_location")
        known_options = {option.option_id for option in application.install_options}
        if len(preferences.selected_options) != len(set(preferences.selected_options)):
            raise ValueError("duplicate_install_option")
        if not set(preferences.selected_options).issubset(known_options):
            raise ValueError("unsupported_install_option")

    def _start(self, task: SoftwareTask) -> SoftwareTask:
        if task.state in TERMINAL_STATES:
            raise ValueError("terminal_task_cannot_start")
        application = get_application(task.application_id)
        try:
            external_id = self.backend.start(
                task.action,
                task.package_name,
                channel=application.channel,
            )
        except SnapdError as error:
            return self._save(task, state="failed", phase="failed", error_code=error.code)
        return self._save(
            task,
            state="running",
            phase="queued",
            progress_percent=0,
            external_id=external_id,
            error_code=None,
        )

    def _save(self, task: SoftwareTask, **changes: object) -> SoftwareTask:
        updated = replace(task, updated_at=self._timestamp(), **changes)
        return self.store.save(updated)

    def _timestamp(self) -> str:
        value = self._now_fn()
        if value.tzinfo is None:
            raise ValueError("clock must return timezone-aware datetime")
        return value.astimezone(UTC).isoformat()
