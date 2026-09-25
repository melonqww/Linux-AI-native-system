from __future__ import annotations

from collections.abc import Callable
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


class CapabilityExecutionError(RuntimeError):
    """Module-owned, stable failure code without exception prose or paths."""

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or not code.isascii() or not code.replace("_", "").isalnum() or len(code) > 64:
            raise ValueError("invalid capability failure code")
        super().__init__(code)
        self.code = code


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
class OperationOutput:
    """Bounded, capability-neutral result returned by an external module."""

    action: str
    item_count: int
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionSnapshot:
    """Trusted server-owned references available to a capability handler."""

    run_id: str
    selection_references: dict[str, str]
    active_selection_id: str | None = None
    last_destination: str | None = None

    def resolve_selection(self, reference: object) -> str:
        if not isinstance(reference, str) or not reference:
            raise ValueError("invalid_results_reference")
        if reference in self.selection_references:
            return self.selection_references[reference]
        if reference == self.active_selection_id:
            return reference
        raise ValueError("results_reference_is_not_trusted")


@dataclass(frozen=True)
class PreparedOperation:
    """Opaque module preparation retained by the core until user consent."""

    approval_request: ApprovalRequest
    commit_payload: object
    discard: Callable[[object], None] | None = None


@dataclass(frozen=True)
class StepExecution:
    step_id: str
    capability: str
    state: StepState
    output: SearchOutput | CopyOutput | OperationOutput | None = None
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
