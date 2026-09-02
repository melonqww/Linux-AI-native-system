"""Strict JSON scenario loading without adding a configuration dependency."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .contracts import ApprovalDecision, FaultEffect, FaultSpec, Scenario, ScenarioTurn
from .faults import FaultController


_ID = re.compile(r"[a-z0-9][a-z0-9_-]{1,79}")


def load_scenario(path: Path) -> Scenario:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: scenario must be an object")
    required = {"id", "title", "locale", "tags", "fixture", "turns"}
    if not required <= set(payload) or set(payload) - (required | {"faults"}):
        raise ValueError(f"{path}: scenario fields are invalid")
    scenario_id = _text(payload["id"], "id", 80)
    if not _ID.fullmatch(scenario_id):
        raise ValueError(f"{path}: scenario id is invalid")
    tags = _string_list(payload["tags"], "tags")
    raw_turns = payload["turns"]
    if not isinstance(raw_turns, list) or not raw_turns:
        raise ValueError(f"{path}: turns must be a non-empty list")
    turns: list[ScenarioTurn] = []
    for ordinal, item in enumerate(raw_turns, start=1):
        if not isinstance(item, dict) or set(item) - {"user", "approval", "expect"}:
            raise ValueError(f"{path}: turn {ordinal} fields are invalid")
        expect = item.get("expect", {})
        if not isinstance(expect, dict):
            raise ValueError(f"{path}: turn {ordinal} expectation must be an object")
        try:
            approval = ApprovalDecision(item.get("approval", "none"))
        except ValueError as error:
            raise ValueError(f"{path}: turn {ordinal} approval is invalid") from error
        turns.append(
            ScenarioTurn(
                _text(item.get("user"), "user", 4_000),
                approval,
                dict(expect),
            )
        )
    faults = _faults(payload.get("faults", []), path)
    return Scenario(
        scenario_id,
        _text(payload["title"], "title", 200),
        _text(payload["locale"], "locale", 16),
        tags,
        _text(payload["fixture"], "fixture", 120),
        tuple(turns),
        path.resolve(),
        faults,
    )


def discover_scenarios(directory: Path, *, suite: str = "full") -> tuple[Scenario, ...]:
    scenarios = tuple(load_scenario(path) for path in sorted(directory.glob("*.json")))
    if suite == "full":
        return scenarios
    return tuple(item for item in scenarios if suite in item.tags)


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
    return tuple(dict.fromkeys(_text(item, label, 80) for item in value))


def _faults(value: object, path: Path) -> tuple[FaultSpec, ...]:
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError(f"{path}: faults must be a list of at most 20 items")
    result: list[FaultSpec] = []
    identities: set[tuple[str, int]] = set()
    for ordinal, item in enumerate(value, start=1):
        if not isinstance(item, dict) or set(item) - {
            "point",
            "occurrence",
            "effect",
            "delay_ms",
        }:
            raise ValueError(f"{path}: fault {ordinal} fields are invalid")
        point = _text(item.get("point"), "fault point", 100)
        if point not in FaultController.ALLOWED_POINTS:
            raise ValueError(f"{path}: fault {ordinal} point is invalid")
        occurrence = item.get("occurrence", 1)
        delay_ms = item.get("delay_ms", 0)
        if isinstance(occurrence, bool) or not isinstance(occurrence, int) or not 1 <= occurrence <= 20:
            raise ValueError(f"{path}: fault {ordinal} occurrence is invalid")
        if isinstance(delay_ms, bool) or not isinstance(delay_ms, int) or not 0 <= delay_ms <= 5_000:
            raise ValueError(f"{path}: fault {ordinal} delay is invalid")
        try:
            effect = FaultEffect(item.get("effect"))
        except (TypeError, ValueError) as error:
            raise ValueError(f"{path}: fault {ordinal} effect is invalid") from error
        if effect is FaultEffect.DELAY and delay_ms == 0:
            raise ValueError(f"{path}: delayed fault requires delay_ms")
        if effect is not FaultEffect.DELAY and delay_ms != 0:
            raise ValueError(f"{path}: delay_ms is valid only for delay faults")
        if effect is FaultEffect.MALFORMED and point != "model.classify_turn":
            raise ValueError(f"{path}: malformed fault point is invalid")
        identity = (point, occurrence)
        if identity in identities:
            raise ValueError(f"{path}: duplicate fault trigger")
        identities.add(identity)
        result.append(FaultSpec(point, occurrence, effect, delay_ms))
    return tuple(result)
