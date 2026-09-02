import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import ai_scenario_lab.campaign as campaign_module
from ai_scenario_lab.adaptive_journeys import load_journey
from ai_scenario_lab.campaign import (
    CampaignRunner,
    _journey_cases,
    _journey_evidence,
    _json_value,
)
from ai_scenario_lab.cli import parser
from ai_scenario_lab.personas import persona_matrix
from ai_scenario_lab.adaptive_journeys import ObservedEffect


LAB_ROOT = Path(__file__).resolve().parents[1]


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
            checks=(
                SimpleNamespace(
                    name="required_effect:file_copy:*", passed=False
                ),
            )
        ),
    )

    assert _journey_evidence(outcome) == {
        "code": "intent_not_recognized",
        "component": "router",
        "check_name": "required_effect:file_copy:*",
    }


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
