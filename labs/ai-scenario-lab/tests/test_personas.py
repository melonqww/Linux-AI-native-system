import pytest

from ai_scenario_lab.personas import (
    PersonaBehavior,
    PersonaProfile,
    UserLanguage,
    persona_matrix,
)


def test_persona_matrix_isolated_by_language_and_behavior():
    profiles = persona_matrix()

    assert len(profiles) == len(UserLanguage) * (len(PersonaBehavior) + 1)
    assert len({profile.persona_id for profile in profiles}) == len(profiles)
    assert {profile.language for profile in profiles} == set(UserLanguage)
    assert all(len(profile.behaviors) <= 1 for profile in profiles)


def test_persona_exposes_separate_coverage_dimensions():
    profile = PersonaProfile(
        "ru-cautious-typo",
        UserLanguage.RU,
        (PersonaBehavior.CAUTIOUS, PersonaBehavior.TYPO),
    )

    assert profile.coverage_dimensions == {
        "persona": "ru-cautious-typo",
        "language": "ru",
        "behaviors": ["cautious", "typo"],
    }


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"persona_id": " ", "language": UserLanguage.RU}, ValueError),
        ({"persona_id": 7, "language": UserLanguage.RU}, TypeError),
        ({"persona_id": "bad", "language": "ru"}, TypeError),
        (
            {
                "persona_id": "duplicate",
                "language": UserLanguage.EN,
                "behaviors": (PersonaBehavior.TYPO, PersonaBehavior.TYPO),
            },
            ValueError,
        ),
    ],
)
def test_persona_contract_rejects_ambiguous_values(kwargs, error):
    with pytest.raises(error):
        PersonaProfile(**kwargs)
