from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ai_native_query import QueryResult, SearchCoverage


class OrchestrationState(StrEnum):
    COMPLETED = "completed"
    AWAITING_APPROVAL = "awaiting_approval"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepState(StrEnum):
    COMPLETED = "completed"
    AWAITING_APPROVAL = "awaiting_approval"
    NOT_STARTED = "not_started"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class SearchOutput:
    collection_id: str
    result_count: int
    results: tuple[QueryResult, ...]
    warnings: tuple[str, ...] = ()
    mode: str = "metadata"
    criteria: str = ""
    coverage: SearchCoverage | None = None
    total_matches: int | None = None
    total_is_exact: bool = True


@dataclass(frozen=True)
class CopyOutput:
    destination: str
    copied_count: int
    copied_paths: tuple[str, ...]


@dataclass(frozen=True)
class ApprovalRequest:
    approval_request_id: str
    plan_id: str
    step_id: str
    action: str
    destination: str
    item_count: int
    total_bytes: int
    item_names: tuple[str, ...]
    expires_in_seconds: int


@dataclass(frozen=True)
class StepExecution:
    step_id: str
    capability: str
    state: StepState
    output: SearchOutput | CopyOutput | None = None
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
    approval_request: ApprovalRequest | None = None
