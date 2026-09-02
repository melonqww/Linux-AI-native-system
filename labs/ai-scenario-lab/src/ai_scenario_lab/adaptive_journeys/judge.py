"""Optional semantic quality judging, intentionally outside safety evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .contracts import Observation


@dataclass(frozen=True)
class ConversationTurn:
    user_text: str
    observation: Observation


@dataclass(frozen=True)
class QualityJudgeInput:
    goal_description: str
    locale: str
    turns: tuple[ConversationTurn, ...]


@dataclass(frozen=True)
class QualityAssessment:
    coherent: bool
    helpful: bool
    notes: tuple[str, ...] = ()


class QualityJudge(Protocol):
    """A judge sees UI conversation only; safety/effects are absent by design."""

    def assess(self, request: QualityJudgeInput) -> QualityAssessment: ...


@dataclass(frozen=True)
class JourneyAssessment:
    deterministic_passed: bool
    quality: QualityAssessment | None

    @property
    def passed(self) -> bool:
        """Qualitative judgement can annotate but never override hard checks."""

        return self.deterministic_passed
