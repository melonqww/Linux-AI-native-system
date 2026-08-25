"""Stable values returned to the runtime and desktop panel."""

from dataclasses import dataclass, field
from enum import StrEnum


class ContentIndexState(StrEnum):
    INDEXED = "indexed"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ContentIndexStatus:
    path: str
    state: ContentIndexState
    reason: str | None = None


@dataclass(frozen=True)
class SearchHit:
    path: str
    line_start: int
    line_end: int
    content: str
    score: float


@dataclass
class IndexReport:
    root: str
    indexed: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def add_skipped(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1
