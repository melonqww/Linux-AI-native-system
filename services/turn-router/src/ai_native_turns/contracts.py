from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TurnKind(StrEnum):
    CONVERSATION = "conversation"
    ACTION = "action"
    MIXED = "mixed"
    CLARIFICATION = "clarification"


@dataclass(frozen=True)
class TurnHistoryMessage:
    role: str
    content: str


@dataclass(frozen=True)
class TurnRequest:
    user_text: str
    locale: str
    history: tuple[TurnHistoryMessage, ...] = ()


@dataclass(frozen=True)
class CapabilityDescriptor:
    capability_id: str
    operation: str
    description: str
    examples: tuple[str, ...]


@dataclass(frozen=True)
class CapabilityMatch:
    capability_id: str
    operation: str
    score: float


@dataclass(frozen=True)
class TurnClassification:
    kind: TurnKind
    language: str
    confidence: float
    conversation_text: str | None = None
    action_text: str | None = None
