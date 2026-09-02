"""Bounded, replayable persona matrices for adaptive user journeys.

The case builder is intentionally independent from the current runner and
reporter.  It produces ordinary :class:`JourneySpec` objects that a later
adapter can execute while retaining their source goal and dimensions.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .adaptive_journeys.contracts import (
    ActionKind,
    GoalSpec,
    JourneyAction,
    JourneyDimensions,
    JourneySpec,
    JourneyState,
)
from .mutations import IntentGoalMetadata, MutatedUtterance, MutationEngine
from .personas import PersonaBehavior, PersonaProfile, UserLanguage


_MUTABLE_TEXT_ACTIONS = {
    ActionKind.FOLLOW_UP,
    ActionKind.CORRECT,
    ActionKind.REPHRASE,
}
_MAX_CASE_LIMIT = 10_000


@dataclass(frozen=True, slots=True)
class JourneyCase:
    """One journey/persona pairing and its traceable surface mutations."""

    case_id: str
    journey: JourneySpec
    persona: PersonaProfile
    source_goal: GoalSpec
    source_dimensions: JourneyDimensions
    mutations: Mapping[str, MutatedUtterance]
    seed: int

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id:
            raise ValueError("case_id must be a non-empty string")
        if not isinstance(self.journey, JourneySpec):
            raise TypeError("journey must be a JourneySpec")
        if not isinstance(self.persona, PersonaProfile):
            raise TypeError("persona must be a PersonaProfile")
        if not isinstance(self.source_goal, GoalSpec):
            raise TypeError("source_goal must be a GoalSpec")
        if not isinstance(self.source_dimensions, JourneyDimensions):
            raise TypeError("source_dimensions must be JourneyDimensions")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise TypeError("seed must be an integer")
        object.__setattr__(self, "mutations", MappingProxyType(dict(self.mutations)))

    @property
    def dimensions(self) -> dict[str, object]:
        """Return separated source and persona dimensions for aggregation."""

        return {
            "language": self.persona.language.value,
            "behaviors": [item.value for item in self.persona.behaviors],
            "mode": self.source_dimensions.mode,
            "memory_depth": self.source_dimensions.memory_depth,
            "input_kind": self.source_dimensions.input_kind,
            "source_language": self.source_dimensions.language,
            "source_behaviors": list(self.source_dimensions.behavior),
        }


def build_journey_cases(
    journeys: tuple[JourneySpec, ...],
    personas: tuple[PersonaProfile, ...],
    *,
    max_cases: int,
    seed: int,
    languages: tuple[UserLanguage, ...] | None = None,
    behaviors: tuple[PersonaBehavior, ...] | None = None,
) -> tuple[JourneyCase, ...]:
    """Build a deterministic, bounded cross-product of journeys and personas.

    ``None`` means no filtering.  An empty behaviour tuple selects neutral
    personas only; a non-empty tuple selects personas whose complete behaviour
    set is contained in it.  This keeps composite profiles attributable and
    prevents an unselected behaviour from leaking into a campaign.
    """

    _validate_inputs(journeys, personas, max_cases, seed, languages, behaviors)
    selected_personas = tuple(
        persona
        for persona in personas
        if _persona_selected(persona, languages, behaviors)
    )

    cases: list[JourneyCase] = []
    case_ids: set[str] = set()
    pair_ids: set[tuple[str, str]] = set()
    for journey in journeys:
        for persona in selected_personas:
            pair = (journey.journey_id, persona.persona_id)
            if pair in pair_ids:
                raise ValueError(
                    "duplicate journey/persona pair would produce a duplicate case id: "
                    f"{journey.journey_id!r}, {persona.persona_id!r}"
                )
            pair_ids.add(pair)
            if len(cases) >= max_cases:
                continue
            case_seed = _derived_seed(seed, journey.journey_id, persona.persona_id)
            case_id = f"{journey.journey_id}--{persona.persona_id}"
            if case_id in case_ids:
                raise ValueError(f"duplicate case id: {case_id}")
            case_ids.add(case_id)
            cases.append(_build_case(case_id, journey, persona, case_seed))
    return tuple(cases)


def _build_case(
    case_id: str,
    source: JourneySpec,
    persona: PersonaProfile,
    case_seed: int,
) -> JourneyCase:
    mutations: dict[str, MutatedUtterance] = {}
    states: dict[str, JourneyState] = {}
    for state_id, state in source.states.items():
        action = state.action
        if action.kind in _MUTABLE_TEXT_ACTIONS:
            if action.text is None:
                raise ValueError(f"text action {state_id!r} has no text")
            metadata = IntentGoalMetadata(
                intent=action.kind.value,
                goal=source.goal.description,
                attributes={
                    "journey_id": source.journey_id,
                    "state_id": state_id,
                },
            )
            mutation = MutationEngine(
                _derived_seed(
                    case_seed, source.journey_id, persona.persona_id, state_id
                )
            ).mutate(action.text, persona=persona, metadata=metadata)
            mutations[state_id] = mutation
            action = JourneyAction(action.kind, mutation.text)
        states[state_id] = JourneyState(state.state_id, action, state.transitions)

    dimensions = JourneyDimensions(
        language=persona.language.value,
        behavior=tuple(item.value for item in persona.behaviors),
        mode=source.dimensions.mode,
        memory_depth=source.dimensions.memory_depth,
        input_kind=source.dimensions.input_kind,
    )
    mutated = JourneySpec(
        journey_id=source.journey_id,
        title=source.title,
        tags=source.tags,
        fixture=source.fixture,
        dimensions=dimensions,
        goal=source.goal,
        max_turns=source.max_turns,
        initial_state=source.initial_state,
        states=states,
        source=source.source,
    )
    return JourneyCase(
        case_id=case_id,
        journey=mutated,
        persona=persona,
        source_goal=source.goal,
        source_dimensions=source.dimensions,
        mutations=mutations,
        seed=case_seed,
    )


def _persona_selected(
    persona: PersonaProfile,
    languages: tuple[UserLanguage, ...] | None,
    behaviors: tuple[PersonaBehavior, ...] | None,
) -> bool:
    if languages is not None and persona.language not in languages:
        return False
    if behaviors is None:
        return True
    if not behaviors:
        return not persona.behaviors
    return bool(persona.behaviors) and set(persona.behaviors).issubset(behaviors)


def _validate_inputs(
    journeys: tuple[JourneySpec, ...],
    personas: tuple[PersonaProfile, ...],
    max_cases: int,
    seed: int,
    languages: tuple[UserLanguage, ...] | None,
    behaviors: tuple[PersonaBehavior, ...] | None,
) -> None:
    if not isinstance(journeys, tuple) or any(
        not isinstance(item, JourneySpec) for item in journeys
    ):
        raise TypeError("journeys must be a tuple of JourneySpec values")
    if not isinstance(personas, tuple) or any(
        not isinstance(item, PersonaProfile) for item in personas
    ):
        raise TypeError("personas must be a tuple of PersonaProfile values")
    if isinstance(max_cases, bool) or not isinstance(max_cases, int):
        raise TypeError("max_cases must be an integer")
    if not 1 <= max_cases <= _MAX_CASE_LIMIT:
        raise ValueError(f"max_cases must be between 1 and {_MAX_CASE_LIMIT}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer")
    _validate_filter(languages, UserLanguage, "languages")
    _validate_filter(behaviors, PersonaBehavior, "behaviors")


def _validate_filter(
    values: tuple[object, ...] | None, enum_type: type, name: str
) -> None:
    if values is None:
        return
    if not isinstance(values, tuple) or any(
        not isinstance(item, enum_type) for item in values
    ):
        raise TypeError(f"{name} must be a tuple of {enum_type.__name__} values")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must not contain duplicates")


def _derived_seed(seed: int, *parts: str) -> int:
    material = "\0".join((str(seed), *parts)).encode("utf-8")
    return int.from_bytes(hashlib.blake2b(material, digest_size=8).digest(), "big")
