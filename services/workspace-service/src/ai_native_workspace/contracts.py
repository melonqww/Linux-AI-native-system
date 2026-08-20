from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessageKind(StrEnum):
    CONVERSATION = "conversation"
    TASK_RESULT = "task_result"
    CLARIFICATION = "clarification"
    NOTICE = "notice"


class WorkspaceStage(StrEnum):
    RECEIVED = "received"
    UNDERSTANDING = "understanding"
    PLANNING = "planning"
    EXECUTING = "executing"
    AWAITING_APPROVAL = "awaiting_approval"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STAGES = frozenset(
    {WorkspaceStage.COMPLETED, WorkspaceStage.FAILED, WorkspaceStage.CANCELLED}
)


@dataclass(frozen=True)
class WorkspaceMessage:
    message_id: str
    role: MessageRole
    kind: MessageKind
    content: str
    created_at: str
    expires_at: str
    task_id: str | None = None


@dataclass(frozen=True)
class WorkspaceRun:
    run_id: str
    stage: WorkspaceStage
    stage_label: str
    created_at: str
    updated_at: str
    started_at: str
    finished_at: str | None
    elapsed_ms: int
    stage_elapsed_ms: int
    user_message_id: str | None
    assistant_message_id: str | None
    task_id: str | None
    approval_request_id: str | None = None


class WorkspaceError(RuntimeError):
    pass


class WorkspaceRunNotFound(WorkspaceError):
    pass


class WorkspaceTransitionError(WorkspaceError):
    pass
