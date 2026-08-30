from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Protocol
from uuid import uuid4

from .catalog import get_application, list_applications
from .contracts import (
    Application,
    BackendProgress,
    InstallPreferences,
    RemovalPreferences,
    SoftwareTask,
    TASK_ACTIONS,
    TERMINAL_STATES,
)
from .notifications import NotificationDeliveryError
from .snapd import SnapdError
from .store import SoftwareTaskStore


ACTIVE_BACKEND_STATES = frozenset({"running", "recovering", "pausing", "canceling"})
RETRY_DELAYS_SECONDS = (5, 15, 30, 60, 120, 300, 300, 300)


class SoftwareBackend(Protocol):
    def start(
        self,
        action: str,
        package_name: str,
        *,
        channel: str = "stable",
        purge: bool = False,
    ) -> str: ...

    def progress(self, change_id: str) -> BackendProgress: ...

    def abort(self, change_id: str) -> None: ...


class ConnectivityMonitor(Protocol):
    def is_online(self) -> bool | None: ...


class DesktopNotifier(Protocol):
    def notify(self, title: str, body: str) -> None: ...


class SoftwareManager:
    def __init__(
        self,
        store: SoftwareTaskStore,
        backend: SoftwareBackend,
        *,
        now_fn: Callable[[], datetime] | None = None,
        id_fn: Callable[[], str] | None = None,
        on_remove_completed: Callable[[str], object] | None = None,
        notifier: DesktopNotifier | None = None,
        connectivity: ConnectivityMonitor | None = None,
        stalled_timeout_seconds: int = 300,
    ) -> None:
        if stalled_timeout_seconds < 30:
            raise ValueError("stalled timeout must be at least 30 seconds")
        self.store = store
        self.backend = backend
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._id_fn = id_fn or (lambda: uuid4().hex)
        self._on_remove_completed = on_remove_completed
        self._notifier = notifier
        self._connectivity = connectivity
        self._stalled_timeout_seconds = stalled_timeout_seconds
        self._operation_lock = threading.RLock()

    def catalog(self) -> tuple[Application, ...]:
        return list_applications()

    def prepare_install(
        self,
        application_id: str,
        preferences: InstallPreferences | None = None,
    ) -> SoftwareTask:
        return self._prepare(application_id, "install", preferences)

    def prepare_remove(
        self,
        application_id: str,
        preferences: RemovalPreferences | None = None,
    ) -> SoftwareTask:
        return self._prepare(
            application_id,
            "remove",
            removal_preferences=preferences or RemovalPreferences(),
        )

    def confirm(self, task_id: str) -> SoftwareTask:
        with self._operation_lock:
            task = self.store.get(task_id)
            if task.state != "awaiting_confirmation":
                raise ValueError("task_not_awaiting_confirmation")
            if task.action == "remove":
                return self._save(
                    task,
                    state="awaiting_final_confirmation",
                    phase="final_confirmation",
                )
            if self._network_status() is False:
                return self._save(
                    task,
                    state="waiting_for_network",
                    phase="waiting_for_network",
                    pause_reason="offline",
                    auto_resume=True,
                    progress_percent=0,
                    last_progress_at=self._timestamp(),
                )
            return self._start(task)

    def confirm_removal(self, task_id: str) -> SoftwareTask:
        with self._operation_lock:
            task = self.store.get(task_id)
            if task.action != "remove" or task.state != "awaiting_final_confirmation":
                raise ValueError("task_not_awaiting_final_removal_confirmation")
            return self._start(task)

    def pause(self, task_id: str) -> SoftwareTask:
        with self._operation_lock:
            task = self.store.get(task_id)
            if task.action != "install":
                raise ValueError("task_cannot_pause")
            if task.state in {"waiting_for_network", "retry_wait", "interrupted"}:
                return self._save(
                    task,
                    state="paused",
                    phase="paused",
                    pause_reason="user",
                    auto_resume=False,
                    next_retry_at=None,
                    download_speed_bps=None,
                    eta_seconds=None,
                    error_code=None,
                )
            if task.state == "pausing" and task.pause_reason != "user":
                return self._save(
                    task,
                    pause_reason="user",
                    auto_resume=False,
                    next_retry_at=None,
                )
            if (
                task.state == "recovering"
                and task.external_id
                and task.phase in {"queued", "downloading", "pause_pending"}
            ):
                return self._request_stop(task, reason="user", target_state="pausing")
            if not task.can_pause or not task.external_id:
                raise ValueError("task_cannot_pause")
            return self._request_stop(task, reason="user", target_state="pausing")

    def resume(self, task_id: str) -> SoftwareTask:
        with self._operation_lock:
            task = self.store.get(task_id)
            if not task.can_resume:
                raise ValueError("task_cannot_resume")
            if self._network_status() is False:
                return self._save(
                    task,
                    state="waiting_for_network",
                    phase="waiting_for_network",
                    pause_reason="offline",
                    auto_resume=True,
                    next_retry_at=None,
                    download_speed_bps=None,
                    eta_seconds=None,
                    error_code=None,
                )
            return self._start(task)

    def cancel(self, task_id: str) -> SoftwareTask:
        with self._operation_lock:
            task = self.store.get(task_id)
            if not task.can_cancel:
                raise ValueError("task_cannot_cancel")
            if task.state in {
                "awaiting_confirmation",
                "awaiting_final_confirmation",
                "paused",
                "interrupted",
                "waiting_for_network",
                "retry_wait",
            } or not task.external_id:
                return self._save(
                    task,
                    state="canceled",
                    phase="canceled",
                    pause_reason="cancel",
                    auto_resume=False,
                    next_retry_at=None,
                    download_speed_bps=None,
                    eta_seconds=None,
                    error_code=None,
                )
            return self._request_stop(task, reason="cancel", target_state="canceling")

    def refresh(self, task_id: str) -> SoftwareTask:
        with self._operation_lock:
            task = self.store.get(task_id)
            if task.state not in ACTIVE_BACKEND_STATES or not task.external_id:
                return self._deliver_notification(task)
            if (
                task.action == "install"
                and task.state in {"running", "recovering"}
                and task.phase in {"queued", "downloading"}
                and self._network_status() is False
            ):
                return self._request_stop(task, reason="offline", target_state="pausing")
            if task.state in {"pausing", "canceling"}:
                self._repeat_stop_request(task)
            try:
                progress = self.backend.progress(task.external_id)
            except SnapdError as error:
                return self._recover_backend_unavailable(task, error.code)
            if task.state == "canceling":
                return self._refresh_canceling(task, progress)
            if task.state == "pausing":
                return self._refresh_pausing(task, progress)
            return self._apply_progress(task, progress)

    def reconcile(self) -> tuple[SoftwareTask, ...]:
        """Reconcile persisted work after boot and during the service polling loop."""
        with self._operation_lock:
            results: list[SoftwareTask] = []
            for persisted in self.store.list():
                task = persisted
                if task.state in ACTIVE_BACKEND_STATES:
                    task = self.refresh(task.task_id)
                online = self._network_status()
                if (
                    task.action == "install"
                    and task.auto_resume
                    and task.state == "waiting_for_network"
                    and online is True
                ):
                    task = self._start(task)
                elif (
                    task.action == "install"
                    and task.auto_resume
                    and task.state == "retry_wait"
                    and online is not False
                    and self._retry_is_due(task)
                ):
                    task = self._start(task)
                task = self._deliver_notification(task)
                results.append(task)
            return tuple(results)

    def acknowledge_notification(self, task_id: str) -> SoftwareTask:
        task = self.store.get(task_id)
        if not task.completion_notification_pending:
            return task
        return self._save(task, completion_notification_pending=False)

    def dispatch_notifications(self) -> tuple[SoftwareTask, ...]:
        return tuple(self._deliver_notification(task) for task in self.store.list())

    def refresh_active(self) -> tuple[SoftwareTask, ...]:
        return self.reconcile()

    def get(self, task_id: str) -> SoftwareTask:
        return self.store.get(task_id)

    def list_tasks(self) -> tuple[SoftwareTask, ...]:
        return self.store.list()

    def _prepare(
        self,
        application_id: str,
        action: str,
        preferences: InstallPreferences | None = None,
        removal_preferences: RemovalPreferences | None = None,
    ) -> SoftwareTask:
        if action not in TASK_ACTIONS:
            raise ValueError("unsupported_action")
        application = get_application(application_id)
        selected_preferences = preferences or InstallPreferences()
        self._validate_preferences(application, selected_preferences)
        now = self._timestamp()
        task = SoftwareTask(
            schema_version=4,
            task_id=self._id_fn(),
            application_id=application.application_id,
            display_name=application.display_name,
            provider=application.provider,
            package_name=application.package_name,
            action=action,
            preferences=selected_preferences,
            removal_preferences=removal_preferences or RemovalPreferences(),
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
            if task.action == "remove":
                external_id = self.backend.start(
                    task.action,
                    task.package_name,
                    channel=application.channel,
                    purge=not task.removal_preferences.create_backup,
                )
            else:
                external_id = self.backend.start(
                    task.action,
                    task.package_name,
                    channel=application.channel,
                )
        except SnapdError as error:
            if task.action == "install" and self._is_retryable(error.code):
                return self._schedule_retry(task, error.code)
            return self._save(task, state="failed", phase="failed", error_code=error.code)
        now = self._timestamp()
        return self._save(
            task,
            state="running",
            phase="queued",
            progress_percent=max(0, task.progress_percent or 0),
            external_id=external_id,
            downloaded_bytes=task.downloaded_bytes,
            total_bytes=task.total_bytes,
            download_speed_bps=None,
            eta_seconds=None,
            pause_reason=None,
            auto_resume=False,
            next_retry_at=None,
            last_progress_at=now,
            completion_notification_pending=False,
            error_code=None,
        )

    def _request_stop(
        self,
        task: SoftwareTask,
        *,
        reason: str,
        target_state: str,
    ) -> SoftwareTask:
        auto_resume = reason in {"offline", "retry", "restart"}
        requested = self._save(
            task,
            state=target_state,
            phase="waiting_for_network" if reason == "offline" else target_state,
            pause_reason=reason,
            auto_resume=auto_resume,
            next_retry_at=None,
            download_speed_bps=None,
            eta_seconds=None,
            error_code=None,
        )
        try:
            self.backend.abort(task.external_id or "")
        except SnapdError as error:
            return self._save(
                requested,
                state="pausing" if reason != "cancel" else "canceling",
                phase="pause_pending" if reason != "cancel" else "cancel_pending",
                error_code=error.code,
            )
        return requested

    def _repeat_stop_request(self, task: SoftwareTask) -> None:
        if not task.external_id:
            return
        try:
            self.backend.abort(task.external_id)
        except SnapdError:
            pass

    def _refresh_pausing(
        self,
        task: SoftwareTask,
        progress: BackendProgress,
    ) -> SoftwareTask:
        if progress.state == "completed":
            return self._apply_progress(task, progress)
        if progress.state in {"interrupted", "canceled", "failed"}:
            if task.pause_reason == "user":
                state, phase, auto_resume = "paused", "paused", False
            elif task.pause_reason == "offline":
                state, phase, auto_resume = "waiting_for_network", "waiting_for_network", True
            else:
                return self._schedule_retry(task, progress.error_code or "change_interrupted")
            return self._save(
                task,
                state=state,
                phase=phase,
                auto_resume=auto_resume,
                download_speed_bps=None,
                eta_seconds=None,
                error_code=None,
            )
        return self._save(
            task,
            progress_percent=self._monotonic_percent(task, progress.progress_percent),
            error_code=progress.error_code,
        )

    def _refresh_canceling(
        self,
        task: SoftwareTask,
        progress: BackendProgress,
    ) -> SoftwareTask:
        if progress.state == "completed":
            return self._apply_progress(task, progress)
        if progress.state in {"interrupted", "canceled", "failed"}:
            return self._save(
                task,
                state="canceled",
                phase="canceled",
                pause_reason="cancel",
                auto_resume=False,
                next_retry_at=None,
                download_speed_bps=None,
                eta_seconds=None,
                error_code=None,
            )
        return task

    def _apply_progress(
        self,
        task: SoftwareTask,
        progress: BackendProgress,
    ) -> SoftwareTask:
        online = self._network_status()
        if task.action == "install" and progress.state in {"failed", "interrupted"} and online is False:
            return self._save(
                task,
                state="waiting_for_network",
                phase="waiting_for_network",
                pause_reason="offline",
                auto_resume=True,
                next_retry_at=None,
                download_speed_bps=None,
                eta_seconds=None,
                error_code=progress.error_code,
            )
        if task.action == "install" and progress.state in {"failed", "interrupted"}:
            retryable = progress.state == "interrupted" or self._is_retryable(progress.error_code)
            if retryable and task.phase in {"queued", "downloading", "recovering"}:
                return self._schedule_retry(task, progress.error_code or "change_interrupted")

        percent = self._monotonic_percent(task, progress.progress_percent)
        downloaded, total, speed, eta = self._download_metrics(task, progress)
        now = self._timestamp()
        made_progress = (
            (percent is not None and (task.progress_percent is None or percent > task.progress_percent))
            or (
                downloaded is not None
                and (task.downloaded_bytes is None or downloaded > task.downloaded_bytes)
            )
        )
        last_progress_at = now if made_progress else task.last_progress_at or now
        completion_notification_pending = (
            task.completion_notification_pending
            or (
                task.action == "install"
                and task.state != "completed"
                and progress.state == "completed"
            )
        )
        updated = self._save(
            task,
            state=progress.state,
            phase=progress.phase,
            progress_percent=percent,
            downloaded_bytes=downloaded,
            total_bytes=total,
            download_speed_bps=speed,
            eta_seconds=eta,
            last_progress_at=last_progress_at,
            retry_count=0 if made_progress else task.retry_count,
            next_retry_at=None if made_progress else task.next_retry_at,
            pause_reason=None if progress.state == "running" else task.pause_reason,
            auto_resume=False if progress.state in TERMINAL_STATES else task.auto_resume,
            completion_notification_pending=completion_notification_pending,
            error_code=progress.error_code,
        )
        if (
            updated.action == "install"
            and updated.state == "running"
            and updated.phase in {"queued", "downloading"}
            and self._is_stalled(updated)
        ):
            return self._request_stop(updated, reason="retry", target_state="pausing")
        if (
            updated.action == "remove"
            and updated.state == "completed"
            and updated.removal_preferences.create_backup
            and self._on_remove_completed is not None
        ):
            try:
                self._on_remove_completed(updated.package_name)
            except Exception:
                pass
        return self._deliver_notification(updated)

    def _recover_backend_unavailable(self, task: SoftwareTask, error_code: str) -> SoftwareTask:
        return self._save(
            task,
            state=(
                task.state
                if task.state in {"pausing", "canceling"}
                else "recovering"
            ),
            phase=task.phase,
            pause_reason=task.pause_reason or (
                "offline" if self._network_status() is False else None
            ),
            auto_resume=task.auto_resume or self._network_status() is False,
            error_code=error_code,
        )

    def _schedule_retry(self, task: SoftwareTask, error_code: str) -> SoftwareTask:
        retry_count = task.retry_count + 1
        if retry_count > len(RETRY_DELAYS_SECONDS):
            return self._save(
                task,
                state="interrupted",
                phase="interrupted",
                pause_reason="retry_limit",
                auto_resume=False,
                next_retry_at=None,
                download_speed_bps=None,
                eta_seconds=None,
                error_code="automatic_retry_exhausted",
            )
        delay = RETRY_DELAYS_SECONDS[retry_count - 1]
        next_retry = self._now() + timedelta(seconds=delay)
        return self._save(
            task,
            state="retry_wait",
            phase="retry_wait",
            pause_reason="retry",
            auto_resume=True,
            retry_count=retry_count,
            next_retry_at=next_retry.isoformat(),
            download_speed_bps=None,
            eta_seconds=None,
            error_code=error_code,
        )

    def _download_metrics(
        self,
        task: SoftwareTask,
        progress: BackendProgress,
    ) -> tuple[int | None, int | None, int | None, int | None]:
        downloaded = progress.downloaded_bytes
        total = progress.total_bytes
        if progress.state == "completed":
            return downloaded, total, None, 0
        if progress.phase != "downloading" or downloaded is None or total is None:
            return downloaded, total, None, None
        speed = task.download_speed_bps
        if task.downloaded_bytes is not None and downloaded >= task.downloaded_bytes:
            previous = datetime.fromisoformat(task.updated_at)
            elapsed = (self._now() - previous.astimezone(UTC)).total_seconds()
            delta = downloaded - task.downloaded_bytes
            if elapsed > 0 and delta > 0:
                instant_speed = max(1, round(delta / elapsed))
                speed = instant_speed if speed is None else max(1, round((speed * 2 + instant_speed) / 3))
        eta = None
        if speed is not None and speed > 0:
            eta = max(0, ceil(max(0, total - downloaded) / speed))
        return downloaded, total, speed, eta

    @staticmethod
    def _monotonic_percent(task: SoftwareTask, percent: int | None) -> int | None:
        if task.progress_percent is not None and percent is not None:
            return max(task.progress_percent, percent)
        return task.progress_percent if percent is None else percent

    def _is_stalled(self, task: SoftwareTask) -> bool:
        if not task.last_progress_at:
            return False
        last = datetime.fromisoformat(task.last_progress_at).astimezone(UTC)
        return (self._now() - last).total_seconds() >= self._stalled_timeout_seconds

    def _retry_is_due(self, task: SoftwareTask) -> bool:
        if task.next_retry_at is None:
            return True
        return self._now() >= datetime.fromisoformat(task.next_retry_at).astimezone(UTC)

    def _is_retryable(self, error_code: str | None) -> bool:
        classifier = getattr(self.backend, "is_retryable_error", None)
        return bool(classifier(error_code)) if callable(classifier) else False

    def _network_status(self) -> bool | None:
        if self._connectivity is None:
            return None
        try:
            return self._connectivity.is_online()
        except Exception:
            return None

    def _deliver_notification(self, task: SoftwareTask) -> SoftwareTask:
        notification = task.completion_notification
        if notification is None or self._notifier is None:
            return task
        try:
            self._notifier.notify(notification["title"], notification["body"])
        except NotificationDeliveryError:
            return task
        return self.acknowledge_notification(task.task_id)

    def _save(self, task: SoftwareTask, **changes: object) -> SoftwareTask:
        updated = replace(
            task,
            schema_version=max(4, task.schema_version),
            updated_at=self._timestamp(),
            **changes,
        )
        return self.store.save(updated)

    def _now(self) -> datetime:
        value = self._now_fn()
        if value.tzinfo is None:
            raise ValueError("clock must return timezone-aware datetime")
        return value.astimezone(UTC)

    def _timestamp(self) -> str:
        return self._now().isoformat()
