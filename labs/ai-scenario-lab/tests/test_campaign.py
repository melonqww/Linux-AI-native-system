import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import ai_scenario_lab.campaign as campaign_module
from ai_scenario_lab.adaptive_journeys import load_journey
from ai_scenario_lab.campaign import (
    CampaignRunner,
    _compiler_evidence,
    _executor_evidence,
    _journey_cases,
    _journey_evidence,
    _json_value,
    _scenario_evidence,
    _selected_journeys,
)
from ai_scenario_lab.cli import parser
from ai_scenario_lab.personas import persona_matrix
from ai_scenario_lab.adaptive_journeys import ObservedEffect


LAB_ROOT = Path(__file__).resolve().parents[1]


def test_compiler_trace_evidence_preserves_codes_without_model_payload():
    evidence = _compiler_evidence(
        (
            {
                "kind": "route",
                "response": {"arguments": {"private": "must-not-be-copied"}},
                "compilation": {
                    "state": "needs_clarification",
                    "diagnostics": ["intent_rejected", "invalid_arguments"],
                },
            },
        )
    )

    assert evidence == {
        "code": "invalid_arguments",
        "component": "intent_compiler",
        "stage": "validation",
    }
    assert "private" not in repr(evidence)
    assert _compiler_evidence(
        (
            {
                "compilation": {
                    "diagnostics": ["intent_rejected", "secret=/home/user"],
                }
            },
        )
    )["code"] == "schema_validation"


def test_journey_case_languages_match_the_source_text_language():
    journeys = tuple(
        load_journey(path) for path in sorted((LAB_ROOT / "journeys").glob("*.json"))
    )
    cases = _journey_cases(
        journeys,
        persona_set="all",
        max_cases=100,
        seed=17,
    )
    assert cases
    assert all(
        case.persona.language.value == case.source_dimensions.language for case in cases
    )
    assert {case.persona.persona_id for case in cases} <= {
        item.persona_id for item in persona_matrix()
    }


def test_campaign_cli_has_bounded_safe_defaults():
    arguments = parser().parse_args(["campaign"])
    assert arguments.suite == "full"
    assert arguments.repeat == 1
    assert arguments.persona_set == "standard"
    assert arguments.max_journey_cases == 24
    assert arguments.seed == 0
    assert arguments.journey == []
    assert arguments.skip_scenarios is False


def test_campaign_cli_can_select_only_multistep_continuation_journeys():
    arguments = parser().parse_args(
        [
            "campaign",
            "regression",
            "--skip-scenarios",
            "--journey",
            "ru-correct-and-approve",
            "--journey",
            "en-deny-and-follow-up",
            "--persona-set",
            "all",
        ]
    )
    assert arguments.skip_scenarios is True
    assert arguments.journey == [
        "ru-correct-and-approve",
        "en-deny-and-follow-up",
    ]


def test_targeted_campaign_filters_journeys_and_rejects_unknown_ids():
    journeys = (
        SimpleNamespace(journey_id="first"),
        SimpleNamespace(journey_id="second"),
    )
    assert _selected_journeys(journeys, ("second",)) == (journeys[1],)
    with pytest.raises(ValueError, match="unknown journey ids: missing"):
        _selected_journeys(journeys, ("missing",))


def test_trace_serializer_handles_immutable_trusted_effect_attributes():
    value = _json_value(ObservedEffect("file_copy", "/safe/file", {"size": 42}))
    assert value == {
        "kind": "file_copy",
        "target": "/safe/file",
        "attributes": {"size": 42},
    }


def test_unmet_journey_goal_without_capability_is_a_router_failure():
    outcome = SimpleNamespace(
        containment={"passed": True},
        error=None,
        capabilities=(),
        evaluation=SimpleNamespace(
            checks=(SimpleNamespace(name="required_effect:file_copy:*", passed=False),)
        ),
    )

    assert _journey_evidence(outcome) == {
        "code": "intent_not_recognized",
        "component": "router",
        "check_name": "required_effect:file_copy",
    }


def test_failed_assistant_contract_is_attributed_to_model_without_reading_text():
    outcome = SimpleNamespace(
        containment={"passed": True},
        model_events=(),
        turns=(
            SimpleNamespace(
                executions=(),
                checks=(
                    SimpleNamespace(
                        name="assistant_contains_any",
                        passed=False,
                        expected=["secret expected phrase"],
                        actual="arbitrary model prose",
                    ),
                ),
            ),
        ),
    )

    assert _scenario_evidence(outcome) == {
        "code": "semantic_mismatch",
        "component": "model",
        "check_name": "assistant_contains_any",
    }


def test_failed_execution_step_uses_executor_error_code():
    evidence = _executor_evidence(
        (
            {
                "result": {
                    "steps": [
                        {
                            "state": "failed",
                            "error_code": "invalid_step_arguments",
                            "output": {"private": "not retained"},
                        }
                    ]
                }
            },
        )
    )

    assert evidence == {
        "code": "invalid_step_arguments",
        "component": "executor",
        "stage": "execution",
    }


def test_conversation_instead_of_required_action_is_a_router_failure():
    outcome = SimpleNamespace(
        containment={"passed": True},
        error=None,
        capabilities=("documents.query.search",),
        execution_records=(),
        model_events=(
            {"kind": "respond_chat", "status": "ok", "response": "private prose"},
        ),
        stop_reason=SimpleNamespace(value="terminal_status"),
        evaluation=SimpleNamespace(
            checks=(SimpleNamespace(name="result:copied_count", passed=False),)
        ),
    )

    assert _journey_evidence(outcome) == {
        "code": "conversation_instead_of_action",
        "component": "router",
        "check_name": "result:copied_count",
        "stop_reason": "terminal_status",
        "model_event": "respond_chat",
    }


def test_scenario_level_error_is_attributed_without_retaining_error_prose():
    outcome = SimpleNamespace(
        containment={"passed": True},
        error="private traceback-like error text",
        model_events=(),
        turns=(),
    )

    evidence = _scenario_evidence(outcome)
    assert evidence == {"code": "execution_failed", "component": "execution"}
    assert "private" not in repr(evidence)


def test_structured_model_error_has_priority_over_generic_outcome_error():
    outcome = SimpleNamespace(
        containment={"passed": True},
        error="generic runner error",
        model_events=(
            {
                "kind": "respond_chat",
                "status": "error",
                "error_type": "TimeoutError",
            },
        ),
        turns=(),
    )

    assert _scenario_evidence(outcome) == {
        "code": "model_timeout",
        "component": "model",
        "model_event": "respond_chat",
    }


def test_message_kind_mismatch_remains_unknown_without_producer_evidence():
    outcome = SimpleNamespace(
        containment={"passed": True},
        error=None,
        model_events=(),
        turns=(
            SimpleNamespace(
                executions=(),
                checks=(
                    SimpleNamespace(
                        name="message_kinds",
                        passed=False,
                        expected=["task_result"],
                        actual=["conversation"],
                    ),
                ),
            ),
        ),
    )

    assert _scenario_evidence(outcome) == {
        "code": "unclassified_contract_failure",
        "check_name": "message_kinds",
    }


def test_effect_check_name_does_not_copy_virtual_path_into_diagnostic():
    outcome = SimpleNamespace(
        containment={"passed": True},
        error=None,
        capabilities=("storage.materialize.plan-copy",),
        execution_records=(
            {
                "result": {
                    "steps": [
                        {"state": "failed", "error_code": "executor_error"}
                    ]
                }
            },
        ),
        model_events=(),
        stop_reason=None,
        evaluation=SimpleNamespace(
            checks=(
                SimpleNamespace(
                    name="forbidden_effect:file_copy:/home/user/private.pdf",
                    passed=False,
                ),
            )
        ),
    )

    evidence = _journey_evidence(outcome)
    assert evidence["check_name"] == "forbidden_effect:file_copy"
    assert "/home/user" not in repr(evidence)


def test_campaign_continues_and_publishes_contract_and_journey_results(
    monkeypatch,
):
    scenario = SimpleNamespace(
        scenario_id="contract-one",
        locale="ru",
        tags=("chat",),
        turns=(object(),),
    )
    scenario_outcome = SimpleNamespace(
        passed=True,
        duration_ms=10.0,
        turns=(),
        containment={"passed": True},
        model_events=(),
    )
    case = SimpleNamespace(
        case_id="journey-one--en-standard",
        journey=object(),
        dimensions={
            "language": "en",
            "behaviors": [],
            "mode": "chat",
            "memory_depth": 2,
            "input_kind": "text",
        },
    )
    journey_outcome = SimpleNamespace(
        passed=False,
        duration_ms=20.0,
        capabilities=(),
        effects=(),
        turns=(),
        containment={"passed": True},
        error=None,
        evaluation=None,
    )

    class FakeScenarioRunner:
        def __init__(self, **_kwargs):
            pass

        def run(self, _scenario, *, run_id):
            assert run_id
            return scenario_outcome

    class FakeJourneyRunner:
        def __init__(self, **_kwargs):
            pass

        def run(self, _journey, *, run_id):
            assert run_id
            return journey_outcome

    lab_root = LAB_ROOT / ".runtime" / f"campaign-test-{uuid4()}"
    (lab_root / "journeys").mkdir(parents=True)
    (lab_root / "journeys" / "one.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        campaign_module,
        "discover_scenarios",
        lambda *_args, **_kwargs: (scenario,),
    )
    monkeypatch.setattr(campaign_module, "load_journey", lambda _path: object())
    monkeypatch.setattr(
        campaign_module, "_journey_cases", lambda *_args, **_kwargs: (case,)
    )
    monkeypatch.setattr(campaign_module, "ScenarioRunner", FakeScenarioRunner)
    monkeypatch.setattr(campaign_module, "JourneyRunner", FakeJourneyRunner)

    try:
        report, attempts = CampaignRunner(
            project_root=LAB_ROOT.parents[1],
            lab_root=lab_root,
            model="fake",
            base_url="http://127.0.0.1:11434",
        ).run(
            suite="full",
            repeat=1,
            persona_set="standard",
            max_journey_cases=1,
            seed=0,
        )

        assert len(attempts) == 2
        assert attempts[0].passed is True
        assert attempts[1].passed is False
        assert attempts[1].diagnostic is not None
        assert attempts[1].diagnostic.layer.value == "UNKNOWN"
        assert (report / "summary.json").is_file()
        assert (report / "failures.md").is_file()
        assert "UNKNOWN" in (report / "failures.md").read_text(encoding="utf-8")
    finally:
        resolved = lab_root.resolve()
        assert resolved.is_relative_to((LAB_ROOT / ".runtime").resolve())
        shutil.rmtree(resolved, ignore_errors=True)
