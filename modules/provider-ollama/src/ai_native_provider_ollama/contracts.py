from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ProviderStatus:
    schema_version: int
    provider_id: str
    display_name: str
    state: str
    installed: bool
    managed: bool
    version: str | None
    decision: str
    prompt_required: bool
    progress_percent: int | None
    completed_bytes: int
    total_bytes: int | None
    reason: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
