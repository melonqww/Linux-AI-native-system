"""Types shared between the intent router, policy layer and system tools."""

from dataclasses import dataclass
from enum import StrEnum


class RiskLevel(StrEnum):
    """Risk is assigned by policy, never by a model-provided field."""

    READ_ONLY = "R0"


class SystemMetric(StrEnum):
    DISK = "disk"
    MEMORY = "memory"
    CPU = "cpu"
    BATTERY = "battery"
    PROCESSES = "processes"


@dataclass(frozen=True)
class IntentProposal:
    request_id: str
    intent: str
    metrics: tuple[SystemMetric, ...]
    process_limit: int
    reason: str


@dataclass(frozen=True)
class PolicyDecision:
    request_id: str
    tool: str
    risk_level: RiskLevel
    decision: str
    approval_required: bool
    allowed_scopes: tuple[str, ...]
