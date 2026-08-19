from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TaskState(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    COMPLETED_WITH_SKIPS = "completed_with_skips"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ItemOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"


TERMINAL_STATES = frozenset(
    {
        TaskState.COMPLETED,
        TaskState.COMPLETED_WITH_SKIPS,
        TaskState.FAILED,
        TaskState.CANCELLED,
    }
)


@dataclass(frozen=True)
class ObjectReference:
    reference_id: str
    kind: str
    display_name: str
    locator: str
    outcome: ItemOutcome
    available: bool


@dataclass(frozen=True)
class TaskView:
    task_id: str
    activity: str
    state: TaskState
    created_at: str
    updated_at: str
    total_items: int | None
    processed_count: int
    succeeded_count: int
    skipped_count: int
    cancel_requested: bool
    continue_requested: bool
    can_cancel: bool
    can_continue: bool
    references: tuple[ObjectReference, ...] = ()


@dataclass(frozen=True)
class ResumePoint:
    task_id: str
    checkpoint_version: int
    checkpoint: dict[str, Any]


class LedgerError(RuntimeError):
    """Stable public failure without exposing internal exception details."""


class TaskNotFoundError(LedgerError):
    pass


class InvalidTransitionError(LedgerError):
    pass


class CancellationRequested(LedgerError):
    pass
