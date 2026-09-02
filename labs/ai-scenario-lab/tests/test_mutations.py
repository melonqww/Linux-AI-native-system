import pytest

from ai_scenario_lab.mutations import IntentGoalMetadata, MutationEngine
from ai_scenario_lab.personas import PersonaBehavior, PersonaProfile, UserLanguage


def _profile(language, *behaviors):
    return PersonaProfile("test-user", language, tuple(behaviors))


def test_mutations_are_replayable_and_keep_semantic_metadata():
    metadata = IntentGoalMetadata(
        intent="search_documents",
        goal="find math documents",
        attributes={"extension": "pdf"},
    )
    persona = _profile(
        UserLanguage.RU,
        PersonaBehavior.VERBOSE,
        PersonaBehavior.TYPO,
        PersonaBehavior.NO_PUNCTUATION,
    )

    first = MutationEngine(17).mutate(
        "Найди документы по математике.", persona=persona, metadata=metadata
    )
    second = MutationEngine(17).mutate(
        "Найди документы по математике.", persona=persona, metadata=metadata
    )

    assert first == second
    assert first.original_text == "Найди документы по математике."
    assert first.metadata is metadata
    assert first.metadata.intent == "search_documents"
    assert first.metadata.goal == "find math documents"
    assert first.metadata.attributes == {"extension": "pdf"}
    assert first.applied_behaviors == persona.behaviors


@pytest.mark.parametrize(
    ("language", "text"),
    [
        (UserLanguage.RU, "Пожалуйста, найди документы на компьютере сейчас."),
        (UserLanguage.EN, "Please find the documents on my computer right now."),
        (UserLanguage.MIXED, "Пожалуйста, find documents on my computer сейчас."),
    ],
)
def test_slang_supports_ru_en_and_mixed(language, text):
    result = MutationEngine(3).mutate(
        text,
        persona=_profile(language, PersonaBehavior.SLANG),
        metadata=IntentGoalMetadata("search", "find documents"),
    )

    assert result.text != text
    assert result.applied_behaviors == (PersonaBehavior.SLANG,)
    assert result.language is language


@pytest.mark.parametrize(
    "behavior",
    [
        PersonaBehavior.TYPO,
        PersonaBehavior.NO_PUNCTUATION,
        PersonaBehavior.VERBOSE,
        PersonaBehavior.CAUTIOUS,
        PersonaBehavior.IMPATIENT,
    ],
)
def test_each_surface_mutation_is_observable(behavior):
    original = "Please find documents, now!"
    result = MutationEngine(11).mutate(
        original,
        persona=_profile(UserLanguage.EN, behavior),
        metadata=IntentGoalMetadata("search", "find documents"),
    )

    assert result.text != original
    assert result.applied_behaviors == (behavior,)


def test_unmatched_slang_is_reported_as_requested_but_not_applied():
    result = MutationEngine(4).mutate(
        "Locate every PDF",
        persona=_profile(UserLanguage.EN, PersonaBehavior.SLANG),
        metadata=IntentGoalMetadata("search", "find PDFs"),
    )

    assert result.requested_behaviors == (PersonaBehavior.SLANG,)
    assert result.applied_behaviors == ()
    assert result.text == result.original_text


def test_metadata_attributes_are_defensively_copied_and_immutable():
    source = {"scope": "documents"}
    metadata = IntentGoalMetadata("search", "find files", source)
    source["scope"] = "changed"

    assert metadata.attributes == {"scope": "documents"}
    with pytest.raises(TypeError):
        metadata.attributes["scope"] = "changed"


@pytest.mark.parametrize(
    ("text", "persona", "metadata", "error"),
    [
        (
            " ",
            _profile(UserLanguage.RU),
            IntentGoalMetadata("chat", "reply"),
            ValueError,
        ),
        ("hello", "not-a-persona", IntentGoalMetadata("chat", "reply"), TypeError),
        ("hello", _profile(UserLanguage.EN), "not-metadata", TypeError),
    ],
)
def test_engine_rejects_invalid_inputs(text, persona, metadata, error):
    with pytest.raises(error):
        MutationEngine(1).mutate(text, persona=persona, metadata=metadata)


def test_seed_must_be_an_integer_not_boolean():
    with pytest.raises(TypeError):
        MutationEngine(True)


def test_metadata_requires_string_intent_goal_and_attributes():
    with pytest.raises(TypeError):
        IntentGoalMetadata(1, "find files")
    with pytest.raises(TypeError):
        IntentGoalMetadata("search", "find files", {"count": 2})


def test_text_must_be_a_string():
    with pytest.raises(TypeError):
        MutationEngine(1).mutate(
            42,
            persona=_profile(UserLanguage.EN),
            metadata=IntentGoalMetadata("chat", "reply"),
        )
