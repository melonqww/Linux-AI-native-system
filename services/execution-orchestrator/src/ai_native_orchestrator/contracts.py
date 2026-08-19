from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ai_native_query import QueryResult


class OrchestrationState(StrEnum):
    COMPLETED = "completed"
    AWAITING_APPROVAL = "awaiting_approval"
    FAILED = "failed"


class StepState(StrEnum):
    COMPLETED = "completed"
    AWAITING_APPROVAL = "awaiting_approval"
    NOT_STARTED = "not_started"
    FAILED = "failed"


@dataclass(frozen=True)
class SearchOutput:
    collection_id: str
    result_count: int
    results: tuple[QueryResult, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class StepExecution:
    step_id: str
    capability: str
    state: StepState
    output: SearchOutput | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class OrchestrationResult:
    run_id: str
    plan_id: str
    state: OrchestrationState
    steps: tuple[StepExecution, ...]
    pending_approval_step_ids: tuple[str, ...] = ()
    active_collection_id: str | None = None
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
