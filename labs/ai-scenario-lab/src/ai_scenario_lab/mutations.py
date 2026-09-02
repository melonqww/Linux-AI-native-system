"""Deterministic, intent-preserving utterance mutations for user journeys."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .personas import PersonaBehavior, PersonaProfile, UserLanguage


_WORD_RE = re.compile(r"[^\W\d_]+", flags=re.UNICODE)
_PUNCTUATION_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)

_SLANG: dict[UserLanguage, tuple[tuple[str, str], ...]] = {
    UserLanguage.RU: (
        ("пожалуйста", "плиз"),
        ("документы", "доки"),
        ("документ", "док"),
        ("сейчас", "щас"),
        ("компьютере", "компе"),
    ),
    UserLanguage.EN: (
        ("please", "pls"),
        ("documents", "docs"),
        ("document", "doc"),
        ("computer", "pc"),
        ("right now", "rn"),
    ),
}

_WRAPPERS: dict[PersonaBehavior, dict[UserLanguage, tuple[str, ...]]] = {
    PersonaBehavior.VERBOSE: {
        UserLanguage.RU: (
            "У меня есть небольшая просьба. {text} Это как раз то, что мне сейчас нужно.",
            "Если получится, помоги мне со следующим: {text} Заранее спасибо.",
        ),
        UserLanguage.EN: (
            "I have a small request. {text} That is what I need at the moment.",
            "If possible, could you help me with this: {text} Thanks in advance.",
        ),
    },
    PersonaBehavior.CAUTIOUS: {
        UserLanguage.RU: (
            "Пожалуйста, будь внимателен и ничего лишнего не делай: {text}",
            "Действуй осторожно и сообщи, если что-то неясно: {text}",
        ),
        UserLanguage.EN: (
            "Please be careful and do nothing extra: {text}",
            "Proceed cautiously and tell me if anything is unclear: {text}",
        ),
    },
    PersonaBehavior.IMPATIENT: {
        UserLanguage.RU: ("Быстро: {text}", "Давай скорее, {text}"),
        UserLanguage.EN: ("Quickly: {text}", "Please hurry, {text}"),
    },
}


@dataclass(frozen=True, slots=True)
class IntentGoalMetadata:
    """Ground truth retained across every surface-language mutation."""

    intent: str
    goal: str
    attributes: Mapping[str, str] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not isinstance(self.intent, str):
            raise TypeError("intent must be a string")
        if not self.intent.strip():
            raise ValueError("intent must not be blank")
        if not isinstance(self.goal, str):
            raise TypeError("goal must be a string")
        if not self.goal.strip():
            raise ValueError("goal must not be blank")
        if not isinstance(self.attributes, Mapping):
            raise TypeError("attributes must be a string mapping")
        copied: dict[str, str] = {}
        for key, value in self.attributes.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError("metadata attributes must have string keys and values")
            copied[key] = value
        object.__setattr__(self, "attributes", MappingProxyType(copied))


@dataclass(frozen=True, slots=True)
class MutatedUtterance:
    """Replayable mutation output with its semantic ground truth attached."""

    original_text: str
    text: str
    metadata: IntentGoalMetadata
    persona_id: str
    language: UserLanguage
    requested_behaviors: tuple[PersonaBehavior, ...]
    applied_behaviors: tuple[PersonaBehavior, ...]
    seed: int

    def __post_init__(self) -> None:
        if not isinstance(self.original_text, str):
            raise TypeError("original_text must be a string")
        if not self.original_text.strip():
            raise ValueError("original_text must not be blank")
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if not self.text.strip():
            raise ValueError("text must not be blank")
        if not isinstance(self.metadata, IntentGoalMetadata):
            raise TypeError("metadata must be IntentGoalMetadata")
        if not isinstance(self.persona_id, str):
            raise TypeError("persona_id must be a string")
        if not self.persona_id.strip():
            raise ValueError("persona_id must not be blank")
        if not isinstance(self.language, UserLanguage):
            raise TypeError("language must be a UserLanguage")
        for name, behaviors in (
            ("requested_behaviors", self.requested_behaviors),
            ("applied_behaviors", self.applied_behaviors),
        ):
            if not isinstance(behaviors, tuple) or any(
                not isinstance(item, PersonaBehavior) for item in behaviors
            ):
                raise TypeError(f"{name} must be a tuple of PersonaBehavior values")
        if any(item not in self.requested_behaviors for item in self.applied_behaviors):
            raise ValueError(
                "applied behaviors must be a subset of requested behaviors"
            )
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise TypeError("seed must be an integer")

    @property
    def coverage_dimensions(self) -> dict[str, object]:
        return {
            "persona": self.persona_id,
            "language": self.language.value,
            "requested_behaviors": [item.value for item in self.requested_behaviors],
            "applied_behaviors": [item.value for item in self.applied_behaviors],
        }


class MutationEngine:
    """Apply controlled mutations reproducibly from one integer seed."""

    def __init__(self, seed: int) -> None:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise TypeError("seed must be an integer")
        self.seed = seed

    def mutate(
        self,
        text: str,
        *,
        persona: PersonaProfile,
        metadata: IntentGoalMetadata,
    ) -> MutatedUtterance:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if not text.strip():
            raise ValueError("text must not be blank")
        if not isinstance(persona, PersonaProfile):
            raise TypeError("persona must be a PersonaProfile")
        if not isinstance(metadata, IntentGoalMetadata):
            raise TypeError("metadata must be IntentGoalMetadata")

        rng = random.Random(self.seed)
        mutated = text
        applied: list[PersonaBehavior] = []
        for behavior in persona.behaviors:
            next_text = self._apply(mutated, behavior, persona.language, rng)
            if next_text != mutated:
                applied.append(behavior)
            mutated = next_text

        return MutatedUtterance(
            original_text=text,
            text=mutated,
            metadata=metadata,
            persona_id=persona.persona_id,
            language=persona.language,
            requested_behaviors=persona.behaviors,
            applied_behaviors=tuple(applied),
            seed=self.seed,
        )

    @staticmethod
    def _apply(
        text: str,
        behavior: PersonaBehavior,
        language: UserLanguage,
        rng: random.Random,
    ) -> str:
        if behavior is PersonaBehavior.TYPO:
            return _add_typo(text, rng)
        if behavior is PersonaBehavior.SLANG:
            return _add_slang(text, language, rng)
        if behavior is PersonaBehavior.NO_PUNCTUATION:
            return " ".join(_PUNCTUATION_RE.sub(" ", text).split())
        if behavior in _WRAPPERS:
            return _wrap(text, behavior, language, rng)
        raise ValueError(f"unsupported persona behavior: {behavior!r}")


def _language_choices(language: UserLanguage) -> tuple[UserLanguage, ...]:
    if language is UserLanguage.MIXED:
        return (UserLanguage.RU, UserLanguage.EN)
    return (language,)


def _add_typo(text: str, rng: random.Random) -> str:
    candidates = [match for match in _WORD_RE.finditer(text) if len(match.group()) >= 4]
    if not candidates:
        return text
    word_match = rng.choice(candidates)
    word = word_match.group()
    positions = [
        index for index in range(1, len(word) - 1) if word[index] != word[index + 1]
    ]
    if not positions:
        return text
    index = rng.choice(positions)
    typo = word[:index] + word[index + 1] + word[index] + word[index + 2 :]
    return text[: word_match.start()] + typo + text[word_match.end() :]


def _add_slang(text: str, language: UserLanguage, rng: random.Random) -> str:
    candidates: list[tuple[int, int, str]] = []
    for choice in _language_choices(language):
        for source, replacement in _SLANG[choice]:
            pattern = re.compile(rf"\b{re.escape(source)}\b", flags=re.IGNORECASE)
            for match in pattern.finditer(text):
                candidates.append((match.start(), match.end(), replacement))
    if not candidates:
        return text
    start, end, replacement = rng.choice(candidates)
    return text[:start] + replacement + text[end:]


def _wrap(
    text: str,
    behavior: PersonaBehavior,
    language: UserLanguage,
    rng: random.Random,
) -> str:
    choices: list[str] = []
    for choice in _language_choices(language):
        choices.extend(_WRAPPERS[behavior][choice])
    return rng.choice(choices).format(text=text)
