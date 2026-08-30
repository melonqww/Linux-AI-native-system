from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime


TASK_ACTIONS = frozenset({"install", "remove"})
TERMINAL_STATES = frozenset({"completed", "failed", "canceled"})
RESUMABLE_STATES = frozenset({"paused", "interrupted", "retry_wait"})


@dataclass(frozen=True)
class ApplicationOption:
    option_id: str
    display_name: str
    description: str
    default_enabled: bool = False


@dataclass(frozen=True)
class InstallPreferences:
    locale: str = "system"
    install_location: str = "default"
    selected_options: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RemovalPreferences:
    create_backup: bool = True

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class Application:
    application_id: str
    display_name: str
    package_name: str
    category: str
    description: str
    store_url: str
    provider: str = "snap"
    channel: str = "stable"
    supported_locales: tuple[str, ...] = ("system",)
    supported_install_locations: tuple[str, ...] = ("default",)
    install_options: tuple[ApplicationOption, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class BackendProgress:
    state: str
    phase: str
    progress_percent: int | None
    error_code: str | None = None
    downloaded_bytes: int | None = None
    total_bytes: int | None = None


@dataclass(frozen=True)
class SoftwareTask:
    schema_version: int
    task_id: str
    application_id: str
    display_name: str
    provider: str
    package_name: str
    action: str
    preferences: InstallPreferences
    removal_preferences: RemovalPreferences
    state: str
    phase: str
    progress_percent: int | None
    external_id: str | None
    created_at: str
    updated_at: str
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    download_speed_bps: int | None = None
    eta_seconds: int | None = None
    completion_notification_pending: bool = False
    pause_reason: str | None = None
    auto_resume: bool = False
    retry_count: int = 0
    next_retry_at: str | None = None
    last_progress_at: str | None = None
    error_code: str | None = None

    @property
    def can_pause(self) -> bool:
        return self.action == "install" and (
            self.state in {"waiting_for_network", "retry_wait"}
            or (self.state == "pausing" and self.pause_reason != "user")
            or (
                self.state == "running"
                and self.phase in {"queued", "downloading"}
            )
        )

    @property
    def can_resume(self) -> bool:
        return self.action == "install" and self.state in RESUMABLE_STATES

    @property
    def can_cancel(self) -> bool:
        return self.state in {
            "awaiting_confirmation",
            "awaiting_final_confirmation",
            "running",
            "recovering",
            "pausing",
            "canceling",
            "paused",
            "interrupted",
            "waiting_for_network",
            "retry_wait",
        }

    @property
    def requires_confirmation(self) -> bool:
        return self.state in {"awaiting_confirmation", "awaiting_final_confirmation"}

    @property
    def requires_final_confirmation(self) -> bool:
        return self.action == "remove" and self.state == "awaiting_final_confirmation"

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload.update(
            can_pause=self.can_pause,
            can_resume=self.can_resume,
            can_cancel=self.can_cancel,
            requires_confirmation=self.requires_confirmation,
            requires_final_confirmation=self.requires_final_confirmation,
            notification=self.completion_notification,
        )
        return payload

    @property
    def completion_notification(self) -> dict[str, str] | None:
        if not self.completion_notification_pending:
            return None
        return {
            "kind": "software_install_completed",
            "title": f"{self.display_name} — установка завершена",
            "body": "Приложение готово к запуску.",
        }


@dataclass(frozen=True)
class SnapshotSummary:
    set_id: int
    package_name: str
    created_at: str
    size_bytes: int | None = None
    automatic: bool = False


@dataclass(frozen=True)
class BackupRecord:
    schema_version: int
    backup_id: str
    application_id: str
    display_name: str
    provider: str
    package_name: str
    set_id: int
    created_at: str
    expires_at: str | None
    expiry_estimated: bool
    size_bytes: int | None
    state: str = "available"
    restore_change_id: str | None = None
    last_restored_at: str | None = None
    error_code: str | None = None

    def remaining_seconds(self, now: datetime) -> int | None:
        if self.expires_at is None:
            return None
        if now.tzinfo is None:
            raise ValueError("clock must return timezone-aware datetime")
        expires = datetime.fromisoformat(self.expires_at).astimezone(UTC)
        return max(0, int((expires - now.astimezone(UTC)).total_seconds()))

    def to_dict(self, *, now: datetime | None = None) -> dict[str, object]:
        payload = asdict(self)
        if now is not None:
            seconds = self.remaining_seconds(now)
            payload["remaining_seconds"] = seconds
            payload["remaining_days"] = (
                None if seconds is None else (seconds + 86_399) // 86_400
            )
        return payload


@dataclass(frozen=True)
class RuntimeStatus:
    state: str
    window_count: int = 0
    process_count: int = 0


@dataclass(frozen=True)
class ApplicationRuntime:
    application_id: str
    desktop_id: str
    state: str
    requested_at: str | None = None

    @property
    def can_launch(self) -> bool:
        return self.state in {"stopped", "launch_failed"}

    @property
    def can_close(self) -> bool:
        return self.state in {"running", "running_in_background"}

    @property
    def can_retry(self) -> bool:
        return self.state == "launch_failed"

    @property
    def status_label(self) -> str:
        labels = {
            "starting": "Запускается",
            "running": "Запущено",
            "running_in_background": "Запущено",
            "stopping": "Закрывается",
            "stopped": "Установлено",
            "launch_failed": "Не удалось запустить",
            "state_unknown": "Состояние неизвестно",
        }
        return labels[self.state]

    @property
    def actions(self) -> tuple[str, ...]:
        if self.can_close:
            return ("close",)
        if self.can_retry:
            return ("retry",)
        if self.can_launch:
            return ("launch",)
        return ()

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload.update(
            status_label=self.status_label,
            actions=self.actions,
            can_launch=self.can_launch,
            can_close=self.can_close,
            can_retry=self.can_retry,
        )
        return payload
