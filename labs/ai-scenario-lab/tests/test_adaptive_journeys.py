from dataclasses import fields, replace
from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4

import pytest
from ai_native_intents import ModelTurn, ModelTurnKind

from ai_scenario_lab.adaptive_journeys import (
    ActionKind,
    JourneyRunner,
    JourneySession,
    Observation,
    ObservedEffect,
    StopReason,
    evaluate_deterministically,
    load_journey,
)
from ai_scenario_lab.adaptive_journeys.judge import (
    JourneyAssessment,
    QualityAssessment,
    QualityJudgeInput,
)


LAB_ROOT = Path(__file__).resolve().parents[1]
JOURNEYS = LAB_ROOT / "journeys"
PROJECT_ROOT = LAB_ROOT.parents[1]


class JourneyProvider:
    def health(self):
        return SimpleNamespace(
            available=True, reason=None, model="fake", version="test"
        )

    def classify_turn(self, request):
        return {
            "kind": "action",
            "language": "ru",
            "confidence": 0.99,
            "conversation_text": None,
            "action_text": request.user_text,
        }

    def route(self, request):
        is_copy = "скопируй" in request.user_text.casefold()
        is_math = "математик" in request.user_text.casefold()
        operation = (
            {
                "id": "copy",
                "kind": "copy_results",
                "arguments": {
                    "results_from": "context.active_results",
                    "destination": "desktop",
                    "directory_name": "Математика",
                },
                "depends_on": [],
                "evidence": [request.user_text],
            }
            if is_copy
            else {
                "id": "search",
                "kind": "search_documents",
                "arguments": (
                    {
                        "mode": "hybrid",
                        "text": "mathematics",
                        "extensions": ["pdf"],
                    }
                    if is_math
                    else {"mode": "metadata", "extensions": ["pdf"]}
                ),
                "depends_on": [],
                "evidence": [request.user_text],
            }
        )
        return ModelTurn(
            ModelTurnKind.ACTION,
            response_text="Выполняю запрос.",
            intent_payload={
                "schema_version": 1,
                "language": "ru",
                "summary": request.user_text,
                "confidence": 0.99,
                "operations": [operation],
            },
        )

    def respond_chat(self, request):
        return "Это тестовый ответ."

    def summarize_result(self, facts, *, locale):
        return "Готово."

    def compose_conversation(self, request, *, system_result):
        return None


def observation(status: str, *, results=(), message="") -> Observation:
    payload = {
        "status": status,
        "messages": ([{"role": "assistant", "text": message}] if message else []),
        "approval": (
            {"prompt": "Allow this operation?", "action_label": "copy"}
            if status == "awaiting_approval"
            else None
        ),
        "results": [{"key": key, "value": value} for key, value in results],
    }
    return Observation.from_public_payload(payload)


def test_all_adaptive_journey_examples_parse_strictly():
    paths = sorted(JOURNEYS.glob("*.json"))
    assert len(paths) == 3
    journeys = [load_journey(path) for path in paths]
    assert len({item.journey_id for item in journeys}) == len(journeys)
    assert {item.dimensions.language for item in journeys} == {"ru", "en"}
    assert {kind for item in journeys for kind in item.dimensions.behavior} >= {
        "corrects_scope",
        "changes_mind",
        "ambiguous",
    }


def test_observation_is_a_strict_user_visible_boundary():
    public = observation("responded", results=(("search_hits", ["a.pdf"]),))
    assert public.results[0].key == "search_hits"

    with pytest.raises(ValueError, match="non-public"):
        Observation.from_public_payload(
            {
                "status": "responded",
                "messages": [],
                "approval": None,
                "results": [],
                "trace": {"model_prompt": "secret"},
            }
        )
    with pytest.raises(ValueError, match="not public"):
        Observation.from_public_payload(
            {
                "status": "responded",
                "messages": [],
                "approval": None,
                "results": [
                    {"key": "search_hits", "value": {"model_prompt": "secret"}}
                ],
            }
        )
    with pytest.raises(ValueError, match="requires a prompt"):
        Observation.from_public_payload(
            {
                "status": "awaiting_approval",
                "messages": [],
                "approval": None,
                "results": [],
            }
        )


def test_policy_reacts_with_correction_followup_and_explicit_approval():
    spec = load_journey(JOURNEYS / "01-ru-correct-and-approve.json")
    session = JourneySession(spec)

    assert session.start().action.kind is ActionKind.FOLLOW_UP
    corrected = session.react(observation("completed", results=(("search_hits", 5),)))
    assert corrected.action.kind is ActionKind.CORRECT
    copy = session.react(observation("completed", results=(("search_hits", 2),)))
    assert copy.action.kind is ActionKind.FOLLOW_UP
    approved = session.react(observation("awaiting_approval"))
    assert approved.action.kind is ActionKind.APPROVE

    stopped = session.react(observation("completed", results=(("copied_count", 2),)))
    assert stopped.stopped is True
    assert stopped.reason is StopReason.TERMINAL_STATUS


def test_policy_can_explicitly_deny_and_stop_without_a_matching_transition():
    spec = load_journey(JOURNEYS / "02-en-deny-and-follow-up.json")
    session = JourneySession(spec)
    session.start()
    denied = session.react(observation("awaiting_approval"))
    assert denied.action.kind is ActionKind.DENY
    no_transition = session.react(observation("responded"))
    assert no_transition.stopped
    assert no_transition.reason is StopReason.NO_TRANSITION


def test_explicit_stop_state_and_max_turn_guard_are_enforced():
    spec = load_journey(JOURNEYS / "03-photo-rephrase-unsupported.json")
    stopped = JourneySession(spec)
    stopped.start()
    decision = stopped.react(observation("responded", message="Я не вижу изображение"))
    assert decision.stopped is True
    assert decision.reason is StopReason.POLICY_STOP

    limited = JourneySession(replace(spec, max_turns=1))
    limited.start()
    decision = limited.react(observation("failed"))
    assert decision.stopped is True
    assert decision.reason is StopReason.MAX_TURNS


def test_forbidden_effect_is_a_hard_failure_that_quality_cannot_override():
    spec = load_journey(JOURNEYS / "02-en-deny-and-follow-up.json")
    final = observation("completed", results=(("cancelled", True),))
    deterministic = evaluate_deterministically(
        spec.goal,
        final,
        (ObservedEffect("file_copy", "/home/test-user/Desktop/Private/a.pdf"),),
    )
    assert deterministic.passed is False
    assert any(
        check.name.startswith("forbidden_effect:file_copy") and not check.passed
        for check in deterministic.checks
    )

    flattering_judge = QualityAssessment(coherent=True, helpful=True)
    combined = JourneyAssessment(deterministic.passed, flattering_judge)
    assert combined.passed is False
    assert "effects" not in {item.name for item in fields(QualityJudgeInput)}


def test_required_effect_counts_and_exact_targets_are_deterministic():
    spec = load_journey(JOURNEYS / "01-ru-correct-and-approve.json")
    final = observation("completed", results=(("copied_count", 1),))
    good = evaluate_deterministically(
        spec.goal,
        final,
        (
            ObservedEffect(
                "file_copy",
                "/home/test-user/Desktop/Математика/algebra.pdf",
            ),
        ),
    )
    assert good.passed

    wrong_extra_file = evaluate_deterministically(
        spec.goal,
        final,
        (
            ObservedEffect(
                "file_copy",
                "/home/test-user/Desktop/Математика/algebra.pdf",
            ),
            ObservedEffect(
                "file_copy",
                "/home/test-user/Desktop/Математика/broken.pdf",
            ),
        ),
    )
    assert not wrong_extra_file.passed


def test_journey_runner_drives_real_workspace_runtime_and_collects_trusted_effects():
    spec = load_journey(JOURNEYS / "01-ru-correct-and-approve.json")
    run_id = f"journey-unit-{uuid4()}"
    runtime_root = LAB_ROOT / ".runtime" / run_id
    runner = JourneyRunner(
        project_root=PROJECT_ROOT,
        lab_root=LAB_ROOT,
        fixture_root=LAB_ROOT / "fixtures",
        provider_factory=JourneyProvider,
    )
    try:
        outcome = runner.run(spec, run_id=run_id)
        assert outcome.passed, outcome
        assert outcome.capabilities == (
            "documents.query.search",
            "storage.materialize.plan-copy",
        )
        assert outcome.model_events
        assert outcome.audit_events
        assert outcome.execution_records
        assert outcome.error is None
        assert outcome.stop_reason is StopReason.TERMINAL_STATUS
        assert [turn.action.kind for turn in outcome.turns] == [
            ActionKind.FOLLOW_UP,
            ActionKind.CORRECT,
            ActionKind.FOLLOW_UP,
            ActionKind.APPROVE,
        ]
        assert outcome.turns[0].observation.status.value == "completed"
        assert outcome.turns[2].observation.status.value == "awaiting_approval"
        assert outcome.turns[-1].observation.status.value == "completed"
        assert outcome.turns[-1].observation.results[0].key == "copied_count"
        assert [
            effect.target for effect in outcome.effects if effect.kind == "file_copy"
        ] == ["/home/test-user/Desktop/Математика/algebra.pdf"]
        assert all(effect.kind != "path_escape" for effect in outcome.effects)
    finally:
        shutil.rmtree(runtime_root, ignore_errors=True)
