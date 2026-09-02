from pathlib import Path

import pytest

from ai_scenario_lab.adaptive_journeys.contracts import (
    ActionKind,
    GoalSpec,
    JourneyAction,
    JourneyDimensions,
    JourneySpec,
    JourneyState,
    ObservationStatus,
)
from ai_scenario_lab.journey_cases import build_journey_cases
from ai_scenario_lab.personas import PersonaBehavior, PersonaProfile, UserLanguage


def _journey(journey_id="find-files"):
    states = {
        "ask": JourneyState(
            "ask", JourneyAction(ActionKind.FOLLOW_UP, "Please find documents.")
        ),
        "correct": JourneyState(
            "correct", JourneyAction(ActionKind.CORRECT, "Only PDF documents.")
        ),
        "rephrase": JourneyState(
            "rephrase", JourneyAction(ActionKind.REPHRASE, "Locate the PDF files.")
        ),
        "approve": JourneyState(
            "approve", JourneyAction(ActionKind.APPROVE, "Yes, copy them.")
        ),
        "deny": JourneyState(
            "deny", JourneyAction(ActionKind.DENY, "No, do not copy them.")
        ),
        "stop": JourneyState("stop", JourneyAction(ActionKind.STOP)),
    }
    return JourneySpec(
        journey_id=journey_id,
        title="Find files",
        tags=("search",),
        fixture="desktop",
        dimensions=JourneyDimensions("en", ("standard",), "action", 5, "text"),
        goal=GoalSpec("Find the requested PDF files", (ObservationStatus.COMPLETED,)),
        max_turns=10,
        initial_state="ask",
        states=states,
        source=Path("journey.json"),
    )


def _persona(persona_id, language, *behaviors):
    return PersonaProfile(persona_id, language, tuple(behaviors))


def test_case_mutates_only_textual_user_actions_and_preserves_ground_truth():
    source = _journey()
    persona = _persona("en-impatient", UserLanguage.EN, PersonaBehavior.IMPATIENT)

    case = build_journey_cases((source,), (persona,), max_cases=1, seed=31)[0]

    assert set(case.mutations) == {"ask", "correct", "rephrase"}
    for state_id in case.mutations:
        mutation = case.mutations[state_id]
        assert case.journey.states[state_id].action.text == mutation.text
        assert mutation.original_text == source.states[state_id].action.text
        assert mutation.metadata.goal == source.goal.description
        assert mutation.metadata.attributes["state_id"] == state_id
    for state_id in ("approve", "deny", "stop"):
        assert case.journey.states[state_id].action == source.states[state_id].action
    assert case.source_goal is source.goal
    assert case.journey.goal is source.goal
    assert case.source_dimensions is source.dimensions
    assert case.dimensions["source_language"] == "en"
    assert case.dimensions["language"] == "en"
    assert case.dimensions["behaviors"] == ["impatient"]


def test_matrix_is_bounded_filtered_and_ordered_reproducibly():
    journeys = (_journey("first"), _journey("second"))
    personas = (
        _persona("ru-standard", UserLanguage.RU),
        _persona("ru-typo", UserLanguage.RU, PersonaBehavior.TYPO),
        _persona("en-typo", UserLanguage.EN, PersonaBehavior.TYPO),
        _persona("en-slang", UserLanguage.EN, PersonaBehavior.SLANG),
    )
    kwargs = {
        "max_cases": 2,
        "seed": 99,
        "languages": (UserLanguage.EN,),
        "behaviors": (PersonaBehavior.TYPO, PersonaBehavior.SLANG),
    }

    first = build_journey_cases(journeys, personas, **kwargs)
    replay = build_journey_cases(journeys, personas, **kwargs)

    assert first == replay
    assert [case.case_id for case in first] == ["first--en-typo", "first--en-slang"]
    assert len(first) == 2
    assert all(case.persona.language is UserLanguage.EN for case in first)


def test_empty_behavior_filter_selects_only_neutral_personas():
    cases = build_journey_cases(
        (_journey(),),
        (
            _persona("standard", UserLanguage.EN),
            _persona("typo", UserLanguage.EN, PersonaBehavior.TYPO),
        ),
        max_cases=5,
        seed=1,
        behaviors=(),
    )

    assert [case.persona.persona_id for case in cases] == ["standard"]


def test_duplicate_case_identity_is_rejected_even_when_limit_is_reached():
    duplicate_personas = (
        _persona("same", UserLanguage.EN),
        _persona("same", UserLanguage.EN),
    )

    with pytest.raises(ValueError, match="duplicate journey/persona pair"):
        build_journey_cases((_journey(),), duplicate_personas, max_cases=1, seed=1)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"max_cases": 0, "seed": 1}, ValueError),
        ({"max_cases": True, "seed": 1}, TypeError),
        ({"max_cases": 1, "seed": False}, TypeError),
        ({"max_cases": 1, "seed": 1, "languages": ("en",)}, TypeError),
        (
            {
                "max_cases": 1,
                "seed": 1,
                "behaviors": (PersonaBehavior.TYPO, PersonaBehavior.TYPO),
            },
            ValueError,
        ),
    ],
)
def test_matrix_contract_rejects_invalid_bounds_and_filters(kwargs, error):
    with pytest.raises(error):
        build_journey_cases((_journey(),), (), **kwargs)
