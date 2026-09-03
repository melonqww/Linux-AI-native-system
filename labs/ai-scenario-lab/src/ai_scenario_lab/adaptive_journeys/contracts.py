"""Value objects for adaptive journeys, kept independent of the production SUT."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


_PUBLIC_RESULT_KEYS = frozenset(
    {"search_hits", "copied_count", "cancelled", "unsupported_reason"}
)


class ActionKind(StrEnum):
    FOLLOW_UP = "follow_up"
    APPROVE = "approve"
    DENY = "deny"
    CORRECT = "correct"
    REPHRASE = "rephrase"
    STOP = "stop"


class ObservationStatus(StrEnum):
    READY = "ready"
    RESPONDED = "responded"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    CLARIFICATION = "clarification"


@dataclass(frozen=True)
class VisibleMessage:
    role: str
    text: str


@dataclass(frozen=True)
class ApprovalPrompt:
    prompt: str
    action_label: str | None = None


@dataclass(frozen=True)
class VisibleResult:
    key: str
    value: object


@dataclass(frozen=True)
class Observation:
    """The complete and intentionally narrow view available to the fake user."""

    status: ObservationStatus
    messages: tuple[VisibleMessage, ...] = ()
    approval: ApprovalPrompt | None = None
    results: tuple[VisibleResult, ...] = ()

    @classmethod
    def from_public_payload(cls, payload: Mapping[str, object]) -> Observation:
        """Build from an adapter payload while rejecting accidental trace leakage."""

        allowed = {"status", "messages", "approval", "results"}
        if not isinstance(payload, Mapping) or set(payload) - allowed:
            raise ValueError("observation contains non-public fields")
        try:
            status = ObservationStatus(payload.get("status"))
        except (TypeError, ValueError) as error:
            raise ValueError("observation status is invalid") from error

        raw_messages = payload.get("messages", [])
        if not isinstance(raw_messages, list):
            raise ValueError("observation messages must be a list")
        messages: list[VisibleMessage] = []
        for item in raw_messages:
            if not isinstance(item, Mapping) or set(item) != {"role", "text"}:
                raise ValueError("visible message fields are invalid")
            role = item["role"]
            text = item["text"]
            if (
                role not in {"assistant", "system"}
                or not isinstance(text, str)
                or not text
                or len(text) > 4_000
            ):
                raise ValueError("visible message is invalid")
            messages.append(VisibleMessage(role, text))

        raw_approval = payload.get("approval")
        approval = None
        if raw_approval is not None:
            if not isinstance(raw_approval, Mapping) or set(raw_approval) - {
                "prompt",
                "action_label",
            }:
                raise ValueError("approval prompt fields are invalid")
            prompt = raw_approval.get("prompt")
            label = raw_approval.get("action_label")
            if (
                not isinstance(prompt, str)
                or not prompt
                or len(prompt) > 4_000
                or (label is not None and not isinstance(label, str))
            ):
                raise ValueError("approval prompt is invalid")
            approval = ApprovalPrompt(prompt, label)

        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            raise ValueError("observation results must be a list")
        results: list[VisibleResult] = []
        for item in raw_results:
            if not isinstance(item, Mapping) or set(item) != {"key", "value"}:
                raise ValueError("visible result fields are invalid")
            key = item["key"]
            if key not in _PUBLIC_RESULT_KEYS:
                raise ValueError("visible result key is invalid")
            results.append(VisibleResult(key, _public_result_value(item["value"])))
        if status is ObservationStatus.AWAITING_APPROVAL and approval is None:
            raise ValueError("awaiting_approval observation requires a prompt")
        if status is not ObservationStatus.AWAITING_APPROVAL and approval is not None:
            raise ValueError("approval prompt is valid only while awaiting approval")
        return cls(status, tuple(messages), approval, tuple(results))


def _public_result_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > 4_000 or any(ord(char) < 32 for char in value):
            raise ValueError("visible result value is invalid")
        return value
    if isinstance(value, list) and len(value) <= 100:
        return tuple(_public_result_value(item) for item in value)
    raise ValueError("visible result value is not public")


@dataclass(frozen=True)
class JourneyAction:
    kind: ActionKind
    text: str | None = None


@dataclass(frozen=True)
class ObservationPredicate:
    statuses: tuple[ObservationStatus, ...] = ()
    approval_present: bool | None = None
    message_contains: tuple[str, ...] = ()
    result_keys: tuple[str, ...] = ()

    def matches(self, observation: Observation) -> bool:
        if self.statuses and observation.status not in self.statuses:
            return False
        if self.approval_present is not None:
            if (observation.approval is not None) is not self.approval_present:
                return False
        visible_text = "\n".join(
            message.text for message in observation.messages
        ).casefold()
        if any(
            fragment.casefold() not in visible_text
            for fragment in self.message_contains
        ):
            return False
        keys = {result.key for result in observation.results}
        return all(key in keys for key in self.result_keys)


@dataclass(frozen=True)
class Transition:
    when: ObservationPredicate
    target: str


@dataclass(frozen=True)
class JourneyState:
    state_id: str
    action: JourneyAction
    transitions: tuple[Transition, ...] = ()


@dataclass(frozen=True)
class EffectExpectation:
    kind: str
    target: str | None = None
    minimum: int = 1
    maximum: int | None = None

    def matches(self, effect: ObservedEffect) -> bool:
        return self.kind == effect.kind and (
            self.target is None or self.target == effect.target
        )


@dataclass(frozen=True)
class ObservedEffect:
    """Trusted effect captured by the harness, never supplied by the model."""

    kind: str
    target: str
    attributes: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))


@dataclass(frozen=True)
class GoalSpec:
    description: str
    terminal_statuses: tuple[ObservationStatus, ...]
    required_result_keys: tuple[str, ...] = ()
    required_effects: tuple[EffectExpectation, ...] = ()
    forbidden_effects: tuple[EffectExpectation, ...] = ()


@dataclass(frozen=True)
class JourneyDimensions:
    language: str
    behavior: tuple[str, ...]
    mode: str
    memory_depth: int
    input_kind: str


@dataclass(frozen=True)
class JourneySpec:
    journey_id: str
    title: str
    tags: tuple[str, ...]
    fixture: str
    dimensions: JourneyDimensions
    goal: GoalSpec
    max_turns: int
    initial_state: str
    states: Mapping[str, JourneyState]
    source: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "states", MappingProxyType(dict(self.states)))
