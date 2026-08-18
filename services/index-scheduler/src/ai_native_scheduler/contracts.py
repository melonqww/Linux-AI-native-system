from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EventKind(StrEnum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    RESCAN = "rescan"


@dataclass(frozen=True)
class FileEvent:
    volume_id: str
    path: str
    kind: EventKind
    observed_at: float


class SchedulerState(StrEnum):
    IDLE = "idle"
    UPDATING = "updating"
    PAUSED_LOAD = "paused_load"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class SchedulerStatus:
    state: SchedulerState
    queued: int
    processed: int
    failed: int
    last_error: str | None = None
    active_rescans: int = 0
    inaccessible: int = 0


@dataclass(frozen=True)
class VolumeChange:
    connected: tuple[str, ...]
    disconnected: tuple[str, ...]
