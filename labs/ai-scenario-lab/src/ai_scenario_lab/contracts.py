from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class ApprovalDecision(StrEnum):
    NONE = "none"
    GRANT = "grant"
    DENY = "deny"
    TIMEOUT = "timeout"


class FaultEffect(StrEnum):
    RAISE = "raise"
    TIMEOUT = "timeout"
    MALFORMED = "malformed"
    DELAY = "delay"


@dataclass(frozen=True)
class FaultSpec:
    point: str
    occurrence: int
    effect: FaultEffect
    delay_ms: int = 0


@dataclass(frozen=True)
class ScenarioTurn:
    user: str
    approval: ApprovalDecision = ApprovalDecision.NONE
    expect: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    title: str
    locale: str
    tags: tuple[str, ...]
    fixture: str
    turns: tuple[ScenarioTurn, ...]
    source: Path
    faults: tuple[FaultSpec, ...] = ()


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    expected: object = None
    actual: object = None


@dataclass(frozen=True)
class TurnOutcome:
    ordinal: int
    user: str
    stage: str
    checks: tuple[CheckResult, ...]
    messages: tuple[dict[str, object], ...]
    executions: tuple[dict[str, object], ...]
    approval: dict[str, object] | None
    duration_ms: float = 0.0
    model_calls: int = 0
    faults: tuple[dict[str, object], ...] = ()

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


@dataclass(frozen=True)
class ScenarioOutcome:
    scenario_id: str
    title: str
    passed: bool
    turns: tuple[TurnOutcome, ...]
    model_events: tuple[dict[str, object], ...]
    audit_events: tuple[dict[str, object], ...]
    virtual_pc: str
    error: str | None = None
    tags: tuple[str, ...] = ()
    duration_ms: float = 0.0
    containment: dict[str, object] = field(default_factory=dict)
    fault_events: tuple[dict[str, object], ...] = ()
