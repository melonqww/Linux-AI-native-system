from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModelStatus:
    schema_version: int
    provider: str
    model: str
    state: str
    progress_percent: int | None
    completed_bytes: int
    total_bytes: int | None
    reason: str | None
    auto_download: bool = True

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
