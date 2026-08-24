from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None
IntentValue: TypeAlias = JsonScalar | tuple[str, ...]


class OperationKind(StrEnum):
    SEARCH_DOCUMENTS = "search_documents"
    FIND_APPLICATION = "find_application"
    PLAN_WEB_SEARCH = "plan_web_search"
    PLAN_OPEN_URL = "plan_open_url"
    SAVE_RESULTS = "save_results"
    COPY_RESULTS = "copy_results"


class CompilationState(StrEnum):
    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNAVAILABLE = "unavailable"


class ModelTurnKind(StrEnum):
    CONVERSATION = "conversation"
    ACTION = "action"
    UNSUPPORTED_ACTION = "unsupported_action"


class RiskClass(StrEnum):
    READ_ONLY = "R0"
    REVERSIBLE_WRITE = "R1"


@dataclass(frozen=True)
class TaskContext:
    active_collection_id: str | None = None
    last_destination: str | None = None
    locale: str = "ru"

    def for_model(self) -> dict[str, object]:
        return {
            "has_active_results": self.active_collection_id is not None,
            "has_last_destination": self.last_destination is not None,
            "locale": self.locale,
        }


@dataclass(frozen=True)
class ModelHistoryMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ModelRequest:
    user_text: str
    locale: str
    context: dict[str, object]
    output_schema: dict[str, object]
    instructions: str
    history: tuple[ModelHistoryMessage, ...] = ()
    allowed_operations: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ModelTurn:
    kind: ModelTurnKind
    response_text: str | None = None
    intent_payload: dict[str, object] | None = None
    unsupported_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class OperationIntent:
    operation_id: str
    kind: OperationKind
    arguments: dict[str, IntentValue]
    depends_on: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class UserIntent:
    intent_id: str
    original_text: str
    language: str
    summary: str
    model_confidence: float
    grounded_confidence: float
    operations: tuple[OperationIntent, ...]


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    operation_id: str
    capability: str
    arguments: dict[str, IntentValue]
    depends_on: tuple[str, ...]
    risk: RiskClass
    approval_required: bool


@dataclass(frozen=True)
class ExecutionPlan:
    plan_id: str
    intent_id: str
    state: CompilationState
    steps: tuple[PlanStep, ...]
    required_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    approval_required: bool
    clarification_question: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompilationResult:
    state: CompilationState
    intent: UserIntent | None
    plan: ExecutionPlan | None
    clarification_question: str | None
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
