"""Strict JSON scenario loading without adding a configuration dependency."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .contracts import ApprovalDecision, Scenario, ScenarioTurn


_ID = re.compile(r"[a-z0-9][a-z0-9_-]{1,79}")


def load_scenario(path: Path) -> Scenario:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: scenario must be an object")
    allowed = {"id", "title", "locale", "tags", "fixture", "turns"}
    if set(payload) != allowed:
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
    return Scenario(
        scenario_id,
        _text(payload["title"], "title", 200),
        _text(payload["locale"], "locale", 16),
        tags,
        _text(payload["fixture"], "fixture", 120),
        tuple(turns),
        path.resolve(),
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
