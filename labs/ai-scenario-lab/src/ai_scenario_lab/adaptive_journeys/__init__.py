"""Deterministic, UI-level user journey primitives.

This package deliberately has no dependency on the existing scenario runner.  An
adapter can be added later without letting simulated users observe internal traces.
"""

from .contracts import (
    ActionKind,
    EffectExpectation,
    GoalSpec,
    JourneyAction,
    JourneySpec,
    Observation,
    ObservationStatus,
    ObservedEffect,
)
from .engine import JourneySession, StepDecision, StopReason
from .evaluation import DeterministicEvaluation, evaluate_deterministically
from .parser import load_journey
from .runner import (
    JourneyOutcome,
    JourneyRunner,
    JourneyTurnOutcome,
    TrustedEffectCollector,
    WorkspaceObservationAdapter,
)

__all__ = [
    "ActionKind",
    "DeterministicEvaluation",
    "EffectExpectation",
    "GoalSpec",
    "JourneyAction",
    "JourneySession",
    "JourneySpec",
    "JourneyOutcome",
    "JourneyRunner",
    "JourneyTurnOutcome",
    "Observation",
    "ObservationStatus",
    "ObservedEffect",
    "StepDecision",
    "StopReason",
    "TrustedEffectCollector",
    "WorkspaceObservationAdapter",
    "evaluate_deterministically",
    "load_journey",
]
