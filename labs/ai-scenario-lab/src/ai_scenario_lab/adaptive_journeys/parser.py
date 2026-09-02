"""Strict JSON parser for adaptive journey fixtures."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping

from .contracts import (
    ActionKind,
    EffectExpectation,
    GoalSpec,
    JourneyAction,
    JourneyDimensions,
    JourneySpec,
    JourneyState,
    ObservationPredicate,
    ObservationStatus,
    Transition,
)


_ID = re.compile(r"[a-z0-9][a-z0-9_-]{1,79}")
_TOP_FIELDS = {
    "id",
    "title",
    "tags",
    "fixture",
    "dimensions",
    "goal",
    "max_turns",
    "initial_state",
    "states",
}


def load_journey(path: Path) -> JourneySpec:
    payload = json.loads(path.read_text(encoding="utf-8"))
    _exact_object(payload, _TOP_FIELDS, f"{path}: journey")
    journey_id = _identifier(payload["id"], "journey id")
    dimensions = _dimensions(payload["dimensions"])
    goal = _goal(payload["goal"])
    max_turns = payload["max_turns"]
    if (
        isinstance(max_turns, bool)
        or not isinstance(max_turns, int)
        or not 1 <= max_turns <= 100
    ):
        raise ValueError("max_turns must be an integer from 1 to 100")
    raw_states = payload["states"]
    if not isinstance(raw_states, list) or not raw_states or len(raw_states) > 100:
        raise ValueError("states must be a non-empty list of at most 100 items")
    states = [_state(item) for item in raw_states]
    state_map = {item.state_id: item for item in states}
    if len(state_map) != len(states):
        raise ValueError("state ids must be unique")
    initial_state = _identifier(payload["initial_state"], "initial_state")
    if initial_state not in state_map:
        raise ValueError("initial_state does not exist")
    for state in states:
        for transition in state.transitions:
            if transition.target not in state_map:
                raise ValueError(f"state {state.state_id} targets an unknown state")
            target = state_map[transition.target]
            if target.action.kind in {ActionKind.APPROVE, ActionKind.DENY}:
                if transition.when.approval_present is not True:
                    raise ValueError(
                        "approve/deny transitions must require an approval prompt"
                    )
        if state.action.kind is ActionKind.STOP and state.transitions:
            raise ValueError("stop states cannot have transitions")
    return JourneySpec(
        journey_id=journey_id,
        title=_text(payload["title"], "title", 200),
        tags=_string_list(payload["tags"], "tags"),
        fixture=_text(payload["fixture"], "fixture", 120),
        dimensions=dimensions,
        goal=goal,
        max_turns=max_turns,
        initial_state=initial_state,
        states=state_map,
        source=path.resolve(),
    )


def _dimensions(value: object) -> JourneyDimensions:
    fields = {"language", "behavior", "mode", "memory_depth", "input_kind"}
    item = _exact_object(value, fields, "dimensions")
    depth = item["memory_depth"]
    if isinstance(depth, bool) or not isinstance(depth, int) or not 0 <= depth <= 100:
        raise ValueError("memory_depth must be an integer from 0 to 100")
    return JourneyDimensions(
        _text(item["language"], "language", 16),
        _string_list(item["behavior"], "behavior"),
        _text(item["mode"], "mode", 40),
        depth,
        _text(item["input_kind"], "input_kind", 40),
    )


def _goal(value: object) -> GoalSpec:
    fields = {
        "description",
        "terminal_statuses",
        "required_result_keys",
        "required_effects",
        "forbidden_effects",
    }
    item = _exact_object(value, fields, "goal")
    try:
        terminal = tuple(
            ObservationStatus(name)
            for name in _string_list(item["terminal_statuses"], "terminal_statuses")
        )
    except ValueError as error:
        raise ValueError("goal contains an invalid terminal status") from error
    if not terminal:
        raise ValueError("goal must declare at least one terminal status")
    return GoalSpec(
        _text(item["description"], "goal description", 1_000),
        terminal,
        _string_list(item["required_result_keys"], "required_result_keys"),
        _effects(item["required_effects"], "required_effects"),
        _effects(item["forbidden_effects"], "forbidden_effects"),
    )


def _effects(value: object, label: str) -> tuple[EffectExpectation, ...]:
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError(f"{label} must be a list of at most 100 items")
    result: list[EffectExpectation] = []
    for raw in value:
        item = _exact_object(raw, {"kind", "target", "minimum", "maximum"}, label)
        minimum = item["minimum"]
        maximum = item["maximum"]
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
            raise ValueError(f"{label} minimum is invalid")
        if maximum is not None and (
            isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or maximum < minimum
        ):
            raise ValueError(f"{label} maximum is invalid")
        target = item["target"]
        if target is not None:
            target = _text(target, f"{label} target", 1_000)
        result.append(
            EffectExpectation(
                _text(item["kind"], f"{label} kind", 100), target, minimum, maximum
            )
        )
    return tuple(result)


def _state(value: object) -> JourneyState:
    item = _exact_object(value, {"id", "action", "transitions"}, "state")
    raw_action = _exact_object(item["action"], {"kind", "text"}, "action")
    try:
        kind = ActionKind(raw_action["kind"])
    except (TypeError, ValueError) as error:
        raise ValueError("action kind is invalid") from error
    text = raw_action["text"]
    if kind is ActionKind.STOP:
        if text is not None:
            raise ValueError("stop action text must be null")
    else:
        text = _text(text, "action text", 4_000)
    raw_transitions = item["transitions"]
    if not isinstance(raw_transitions, list) or len(raw_transitions) > 20:
        raise ValueError("transitions must be a list of at most 20 items")
    transitions = tuple(_transition(raw) for raw in raw_transitions)
    return JourneyState(
        _identifier(item["id"], "state id"), JourneyAction(kind, text), transitions
    )


def _transition(value: object) -> Transition:
    item = _exact_object(value, {"when", "target"}, "transition")
    predicate = _exact_object(
        item["when"],
        {"statuses", "approval_present", "message_contains", "result_keys"},
        "transition predicate",
    )
    try:
        statuses = tuple(
            ObservationStatus(name)
            for name in _string_list(predicate["statuses"], "statuses")
        )
    except ValueError as error:
        raise ValueError("transition contains an invalid status") from error
    approval_present = predicate["approval_present"]
    if approval_present is not None and not isinstance(approval_present, bool):
        raise ValueError("approval_present must be boolean or null")
    return Transition(
        ObservationPredicate(
            statuses,
            approval_present,
            _string_list(predicate["message_contains"], "message_contains"),
            _string_list(predicate["result_keys"], "result_keys"),
        ),
        _identifier(item["target"], "transition target"),
    )


def _exact_object(value: object, fields: set[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} fields are invalid")
    return value


def _identifier(value: object, label: str) -> str:
    text = _text(value, label, 80)
    if not _ID.fullmatch(text):
        raise ValueError(f"{label} is invalid")
    return text


def _text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    value = value.strip()
    if not value or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise ValueError(f"{label} is invalid")
    return value


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a string list")
    return tuple(dict.fromkeys(_text(item, label, 200) for item in value))
