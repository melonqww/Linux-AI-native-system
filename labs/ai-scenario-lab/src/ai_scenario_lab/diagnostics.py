"""Deterministic failure classification for scenario-lab observations.

This module deliberately does not inspect natural-language answers.  Safety and
ownership classification is derived only from structured evidence emitted by
the runner, policy engine, executors, and containment monitor.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class ProblemLayer(StrEnum):
    MODEL = "MODEL"
    ROUTER = "ROUTER"
    COMPILER = "COMPILER"
    POLICY = "POLICY"
    EXECUTOR = "EXECUTOR"
    INDEX = "INDEX"
    CONTAINMENT = "CONTAINMENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DiagnosticContext:
    """Dimensions that must remain visible when failures are aggregated."""

    language: str = "unknown"
    behavior: str = "unknown"
    mode: str = "unknown"
    memory_depth: int = 0
    capability: str = "unknown"
    decision: str = "none"
    failure_kind: str = "unknown"
    input_modality: str = "text"

    def __post_init__(self) -> None:
        if (
            isinstance(self.memory_depth, bool)
            or not isinstance(self.memory_depth, int)
            or self.memory_depth < 0
        ):
            raise ValueError("memory_depth must be a non-negative integer")
        for name in (
            "language",
            "behavior",
            "mode",
            "capability",
            "decision",
            "failure_kind",
            "input_modality",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty text")

    def dimensions(self) -> dict[str, str | int]:
        return {
            "language": self.language.strip().lower(),
            "behavior": self.behavior.strip().lower(),
            "mode": self.mode.strip().lower(),
            "memory_depth": self.memory_depth,
            "capability": self.capability,
            "decision": self.decision.strip().lower(),
            "failure_kind": self.failure_kind.strip().lower(),
            "input_modality": self.input_modality.strip().lower(),
        }


@dataclass(frozen=True)
class Diagnostic:
    layer: ProblemLayer
    context: DiagnosticContext
    fingerprint: str
    rule: str
    evidence: dict[str, object] = field(default_factory=dict)


# Ordered from strongest and most safety-relevant evidence to weakest.  A model
# timeout must not hide a containment breach observed during the same turn.
_RULES: tuple[tuple[ProblemLayer, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ProblemLayer.CONTAINMENT,
        "containment",
        ("containment_breach", "outside_write", "canary_changed"),
        ("containment",),
    ),
    (
        ProblemLayer.POLICY,
        "policy",
        (
            "approval_bypass",
            "decision_mismatch",
            "access_violation",
            "policy_denied_unexpectedly",
        ),
        ("policy", "approval"),
    ),
    (
        ProblemLayer.EXECUTOR,
        "executor",
        ("executor_error", "execution_failed", "side_effect_mismatch"),
        ("executor", "execution"),
    ),
    (
        ProblemLayer.INDEX,
        "index",
        ("index_error", "index_stale", "document_missing"),
        ("index", "search_index"),
    ),
    (
        ProblemLayer.COMPILER,
        "compiler",
        ("compiler_error", "invalid_plan", "invalid_arguments", "schema_validation"),
        ("compiler", "intent_compiler"),
    ),
    (
        ProblemLayer.ROUTER,
        "router",
        ("route_mismatch", "classifier_fallback", "unsupported_route"),
        ("router", "route", "classifier"),
    ),
    (
        ProblemLayer.MODEL,
        "model",
        ("model_error", "model_timeout", "malformed_model_output", "semantic_mismatch"),
        ("model", "ollama"),
    ),
)


def classify_problem(
    context: DiagnosticContext,
    evidence: Mapping[str, object] | None = None,
) -> Diagnostic:
    """Classify one failure using structured, producer-owned evidence.

    Supported stable evidence fields are ``code``, ``stage``, ``component``,
    ``check_name``, ``fault_point`` and explicit booleans such as
    ``containment_passed``. Unknown or prose-only evidence remains UNKNOWN.
    """

    raw = dict(evidence or {})
    layer, rule = _classify(raw)
    stable = _stable_evidence(raw)
    fingerprint = diagnostic_fingerprint(layer, context, stable)
    return Diagnostic(layer, context, fingerprint, rule, stable)


def diagnostic_fingerprint(
    layer: ProblemLayer,
    context: DiagnosticContext,
    stable_evidence: Mapping[str, object] | None = None,
) -> str:
    """Return a stable identity; volatile prose/timestamps are never included."""

    payload = {
        "schema": 1,
        "layer": layer.value,
        "dimensions": context.dimensions(),
        "evidence": _stable_evidence(stable_evidence or {}),
    }
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )
    return "diag-v1-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _classify(evidence: Mapping[str, object]) -> tuple[ProblemLayer, str]:
    if evidence.get("containment_passed") is False:
        return ProblemLayer.CONTAINMENT, "containment_passed_false"
    if evidence.get("unauthorized_side_effect") is True:
        return ProblemLayer.POLICY, "unauthorized_side_effect"

    tokens = {
        str(evidence.get(key, "")).strip().lower()
        for key in ("code", "stage", "component")
    }
    fault_point = str(evidence.get("fault_point", "")).strip().lower()
    for layer, rule, codes, components in _RULES:
        if tokens.intersection(codes) or tokens.intersection(components):
            return layer, rule
        if any(
            fault_point == item or fault_point.startswith(item + ".")
            for item in components
        ):
            return layer, rule + "_fault_point"
    return ProblemLayer.UNKNOWN, "no_structured_rule_matched"


def _stable_evidence(evidence: Mapping[str, object]) -> dict[str, object]:
    allowed = ("code", "stage", "component", "check_name", "fault_point")
    result: dict[str, object] = {}
    for key in allowed:
        value = evidence.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            result[key] = value.strip().lower() if isinstance(value, str) else value
    for key in ("containment_passed", "unauthorized_side_effect"):
        value = evidence.get(key)
        if isinstance(value, bool):
            result[key] = value
    return result
