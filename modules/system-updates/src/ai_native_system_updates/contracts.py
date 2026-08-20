"""Bounded JSON contracts for cached Ubuntu update checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class UpdateCheck:
    schema_version: int
    supported: bool
    state: str
    provider: str
    available_count: int
    security_count: int
    package_names: tuple[str, ...]
    cache_age_seconds: int | None
    cache_stale: bool
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
