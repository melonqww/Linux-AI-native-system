from __future__ import annotations

from dataclasses import asdict, dataclass


TASK_ACTIONS = frozenset({"install", "remove"})
TERMINAL_STATES = frozenset({"completed", "failed", "canceled"})
RESUMABLE_STATES = frozenset({"paused", "interrupted"})


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
    state: str
    phase: str
    progress_percent: int | None
    external_id: str | None
    created_at: str
    updated_at: str
    error_code: str | None = None

    @property
    def can_pause(self) -> bool:
        return (
            self.action == "install"
            and self.state == "running"
            and self.phase in {"queued", "downloading"}
        )

    @property
    def can_resume(self) -> bool:
        return self.action == "install" and self.state in RESUMABLE_STATES

    @property
    def can_cancel(self) -> bool:
        return self.state in {
            "awaiting_confirmation",
            "running",
            "paused",
            "interrupted",
        }

    @property
    def requires_confirmation(self) -> bool:
        return self.state == "awaiting_confirmation"

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload.update(
            can_pause=self.can_pause,
            can_resume=self.can_resume,
            can_cancel=self.can_cancel,
            requires_confirmation=self.requires_confirmation,
        )
        return payload
