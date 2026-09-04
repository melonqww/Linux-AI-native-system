"""Autonomous contract + human-journey campaign orchestration."""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .adaptive_journeys import JourneyRunner, load_journey
from .campaign_report import CampaignAttempt, write_campaign_report
from .diagnostics import DiagnosticContext, classify_problem
from .journey_cases import JourneyCase, build_journey_cases
from .personas import PersonaProfile, persona_matrix
from .runner import ScenarioRunner
from .scenario import discover_scenarios


class CampaignRunner:
    """Runs every selected case, records failures, and never fails fast."""

    def __init__(
        self,
        *,
        project_root: Path,
        lab_root: Path,
        model: str,
        base_url: str,
        provider_factory=None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.lab_root = lab_root.resolve()
        self.model = model
        self.base_url = base_url
        self.provider_factory = provider_factory

    def run(
        self,
        *,
        suite: str,
        repeat: int,
        persona_set: str,
        max_journey_cases: int,
        seed: int,
        journey_ids: tuple[str, ...] = (),
        include_scenarios: bool = True,
    ) -> tuple[Path, tuple[CampaignAttempt, ...]]:
        if not 1 <= repeat <= 20:
            raise ValueError("repeat must be between 1 and 20")
        if persona_set not in {"standard", "all"}:
            raise ValueError("persona_set must be standard or all")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        trace_root = self.lab_root / "reports" / "campaign-traces" / stamp
        trace_root.mkdir(parents=True, exist_ok=False)
        attempts: list[CampaignAttempt] = []
        scenarios = (
            discover_scenarios(self.lab_root / "scenarios", suite=suite)
            if include_scenarios
            else ()
        )
        scenario_runner = ScenarioRunner(
            project_root=self.project_root,
            lab_root=self.lab_root,
            fixture_root=self.lab_root / "fixtures",
            model=self.model,
            base_url=self.base_url,
            provider_factory=self.provider_factory,
        )
        for repetition in range(1, repeat + 1):
            for scenario in scenarios:
                attempt_id = f"scenario--{scenario.scenario_id}--r{repetition}"
                print(f"CAMPAIGN RUN  {attempt_id}", flush=True)
                outcome = scenario_runner.run(scenario, run_id=f"{stamp}-{attempt_id}")
                trace = self._write_trace(trace_root, attempt_id, outcome)
                context = _scenario_context(scenario, outcome)
                diagnostic = None
                if not outcome.passed:
                    diagnostic = classify_problem(context, _scenario_evidence(outcome))
                attempts.append(
                    CampaignAttempt(
                        attempt_id,
                        scenario.scenario_id,
                        "scenario",
                        context,
                        outcome.passed,
                        outcome.duration_ms,
                        diagnostic,
                        str(trace),
                    )
                )
                print(
                    f"CAMPAIGN {'PASS' if outcome.passed else 'FAIL'} {attempt_id}",
                    flush=True,
                )

        journeys = tuple(
            load_journey(path)
            for path in sorted((self.lab_root / "journeys").glob("*.json"))
        )
        journeys = _selected_journeys(journeys, journey_ids)
        cases = _journey_cases(
            journeys,
            persona_set=persona_set,
            max_cases=max_journey_cases,
            seed=seed,
        )
        journey_runner = JourneyRunner(
            project_root=self.project_root,
            lab_root=self.lab_root,
            fixture_root=self.lab_root / "fixtures",
            model=self.model,
            base_url=self.base_url,
            provider_factory=self.provider_factory,
        )
        for repetition in range(1, repeat + 1):
            for case in cases:
                attempt_id = f"journey--{case.case_id}--r{repetition}"
                print(f"CAMPAIGN RUN  {attempt_id}", flush=True)
                outcome = journey_runner.run(
                    case.journey, run_id=f"{stamp}-{attempt_id}"
                )
                trace = self._write_trace(trace_root, attempt_id, outcome)
                context = _journey_context(case, outcome)
                diagnostic = None
                if not outcome.passed:
                    diagnostic = classify_problem(context, _journey_evidence(outcome))
                attempts.append(
                    CampaignAttempt(
                        attempt_id,
                        case.case_id,
                        "journey",
                        context,
                        outcome.passed,
                        outcome.duration_ms,
                        diagnostic,
                        str(trace),
                    )
                )
                print(
                    f"CAMPAIGN {'PASS' if outcome.passed else 'FAIL'} {attempt_id}",
                    flush=True,
                )
        report = write_campaign_report(
            self.lab_root / "reports" / "campaigns",
            attempts,
            run_id=stamp,
        )
        (self.lab_root / "reports" / "campaign-latest.txt").write_text(
            stamp, encoding="utf-8"
        )
        return report, tuple(attempts)

    @staticmethod
    def _write_trace(root: Path, attempt_id: str, outcome: object) -> Path:
        target = root / f"{attempt_id}.json"
        target.write_text(
            json.dumps(_json_value(outcome), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target


def _journey_cases(
    journeys,
    *,
    persona_set: str,
    max_cases: int,
    seed: int,
) -> tuple[JourneyCase, ...]:
    profiles = persona_matrix()
    if persona_set == "standard":
        profiles = tuple(item for item in profiles if not item.behaviors)
    groups: list[tuple[JourneyCase, ...]] = []
    for journey in journeys:
        compatible = tuple(
            item
            for item in profiles
            if _language_compatible(journey.dimensions.language, item)
        )
        groups.append(
            build_journey_cases((journey,), compatible, max_cases=max_cases, seed=seed)
        )
    # Round-robin: a small budget must not spend all slots on the first language.
    return tuple(
        group[index]
        for index in range(max((len(group) for group in groups), default=0))
        for group in groups
        if index < len(group)
    )[:max_cases]


def _selected_journeys(journeys, journey_ids: tuple[str, ...]):
    if not journey_ids:
        return tuple(journeys)
    requested_ids = set(journey_ids)
    selected = tuple(
        journey for journey in journeys if journey.journey_id in requested_ids
    )
    missing = requested_ids - {journey.journey_id for journey in selected}
    if missing:
        raise ValueError("unknown journey ids: " + ", ".join(sorted(missing)))
    return selected


def _language_compatible(source: str, persona: PersonaProfile) -> bool:
    # Mutation changes surface style, never translates the source utterance.
    # Keeping the languages equal prevents a Russian sentence from being
    # reported as an English or mixed-language test case.
    return persona.language.value == source


def _scenario_context(scenario, outcome) -> DiagnosticContext:
    expected_capabilities: set[str] = set()
    for turn in outcome.turns:
        for check in turn.checks:
            if check.name == "capabilities" and isinstance(check.expected, list):
                expected_capabilities.update(
                    item for item in check.expected if isinstance(item, str)
                )
    tags = set(scenario.tags)
    mode = "mixed" if "mixed" in tags else "chat" if "chat" in tags else "action"
    decision = (
        "deny"
        if "denial" in tags
        else "timeout"
        if "timeout" in tags
        else "grant"
        if "approval" in tags
        else "none"
    )
    return DiagnosticContext(
        language=scenario.locale,
        behavior=next(
            (item for item in ("negative", "prompt-injection") if item in tags),
            "standard",
        ),
        mode=mode,
        memory_depth=len(scenario.turns),
        capability=",".join(sorted(expected_capabilities)) or "none",
        decision=decision,
        failure_kind="none" if outcome.passed else "contract_failure",
        input_modality="text",
    )


def _scenario_evidence(outcome) -> dict[str, object]:
    if not bool(outcome.containment.get("passed")):
        return {"containment_passed": False, "component": "containment"}
    for turn in outcome.turns:
        for check in turn.checks:
            if (
                check.name == "no_operations"
                and not check.passed
                and check.expected is True
            ):
                return {"code": "unrequested_operation", "component": "router"}
    if any(event.get("status") == "error" for event in outcome.model_events):
        return {"code": "model_error", "component": "model"}
    for turn in outcome.turns:
        if any("error_type" in record for record in turn.executions):
            return {"code": "executor_error", "component": "executor"}
        for check in turn.checks:
            if not check.passed and check.name == "capabilities":
                return {"code": "route_mismatch", "component": "router"}
    return {"code": "unclassified_contract_failure"}


def _journey_context(case: JourneyCase, outcome) -> DiagnosticContext:
    dimensions = case.dimensions
    capability = ",".join(sorted(outcome.capabilities)) or "none"
    decision = next(
        (
            "grant" if turn.action.kind.value == "approve" else "deny"
            for turn in outcome.turns
            if turn.action.kind.value in {"approve", "deny"}
        ),
        "none",
    )
    return DiagnosticContext(
        language=str(dimensions["language"]),
        behavior=",".join(dimensions["behaviors"]) or "standard",
        mode=str(dimensions["mode"]),
        memory_depth=int(dimensions["memory_depth"]),
        capability=capability,
        decision=decision,
        failure_kind="none" if outcome.passed else "journey_goal_failure",
        input_modality=str(dimensions["input_kind"]),
    )


def _journey_evidence(outcome) -> dict[str, object]:
    if not bool(outcome.containment.get("passed")):
        return {"containment_passed": False, "component": "containment"}
    if outcome.error:
        return {"code": "execution_failed", "component": "execution"}
    if outcome.evaluation is not None:
        for check in outcome.evaluation.checks:
            if not check.passed and check.name.startswith("forbidden_effect"):
                return {"unauthorized_side_effect": True, "component": "policy"}
        unmet_goal = next(
            (
                check
                for check in outcome.evaluation.checks
                if not check.passed
                and (
                    check.name.startswith("required_effect")
                    or check.name.startswith("result:")
                )
            ),
            None,
        )
        if unmet_goal is not None and not outcome.capabilities:
            return {
                "code": "intent_not_recognized",
                "component": "router",
                "check_name": unmet_goal.name,
            }
    return {"code": "unclassified_journey_failure"}


def _json_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _json_value(getattr(value, item.name)) for item in fields(value)
        }
    if isinstance(value, MappingProxyType):
        return _json_value(dict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, (Path, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)
