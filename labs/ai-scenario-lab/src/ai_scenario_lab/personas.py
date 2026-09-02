"""Strict user-persona contracts for goal-driven scenario generation.

This module deliberately has no dependency on the scenario runner.  A future
journey runner can consume these profiles without coupling persona generation
to the current contract-test format.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class UserLanguage(StrEnum):
    """Language used by a simulated user."""

    RU = "ru"
    EN = "en"
    MIXED = "mixed"


class PersonaBehavior(StrEnum):
    """Controlled, independently measurable user behaviours."""

    TYPO = "typo"
    SLANG = "slang"
    NO_PUNCTUATION = "no_punctuation"
    VERBOSE = "verbose"
    CAUTIOUS = "cautious"
    IMPATIENT = "impatient"


@dataclass(frozen=True, slots=True)
class PersonaProfile:
    """A stable persona definition used to produce journey utterances.

    Behaviours are a tuple rather than a set so mutation order is explicit and
    reproducible.  Duplicate behaviours would make coverage and replay
    ambiguous, therefore they are rejected.
    """

    persona_id: str
    language: UserLanguage
    behaviors: tuple[PersonaBehavior, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.persona_id, str):
            raise TypeError("persona_id must be a string")
        if not self.persona_id.strip():
            raise ValueError("persona_id must not be blank")
        if self.persona_id != self.persona_id.strip():
            raise ValueError("persona_id must not have surrounding whitespace")
        if not isinstance(self.language, UserLanguage):
            raise TypeError("language must be a UserLanguage")
        if not isinstance(self.behaviors, tuple):
            raise TypeError("behaviors must be a tuple")
        if any(not isinstance(item, PersonaBehavior) for item in self.behaviors):
            raise TypeError("every behavior must be a PersonaBehavior")
        if len(set(self.behaviors)) != len(self.behaviors):
            raise ValueError("persona behaviors must be unique")
        if not isinstance(self.description, str):
            raise TypeError("description must be a string")

    @property
    def coverage_dimensions(self) -> dict[str, object]:
        """Return report-friendly dimensions without runner-specific types."""

        return {
            "persona": self.persona_id,
            "language": self.language.value,
            "behaviors": [behavior.value for behavior in self.behaviors],
        }


def persona_matrix() -> tuple[PersonaProfile, ...]:
    """Return the baseline language/behaviour coverage matrix.

    Each behaviour is isolated intentionally.  Composite profiles can be
    authored separately, while this baseline makes regressions attributable to
    one language and one behavioural dimension.
    """

    profiles: list[PersonaProfile] = []
    for language in UserLanguage:
        profiles.append(
            PersonaProfile(
                persona_id=f"{language.value}-standard",
                language=language,
                description="Neutral user wording",
            )
        )
        for behavior in PersonaBehavior:
            profiles.append(
                PersonaProfile(
                    persona_id=f"{language.value}-{behavior.value}",
                    language=language,
                    behaviors=(behavior,),
                    description=f"Isolated {behavior.value} behaviour",
                )
            )
    return tuple(profiles)
