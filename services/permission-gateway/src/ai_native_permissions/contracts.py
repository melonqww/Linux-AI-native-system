from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias


JsonValue: TypeAlias = str | int | float | bool | None | tuple[str, ...]


class RiskLevel(StrEnum):
    READ_ONLY = "R0"
    REVERSIBLE_WRITE = "R1"
    IMPORTANT_WRITE = "R2"
    PRIVILEGED = "R3"


class ExecutionPhase(StrEnum):
    EXECUTE = "execute"
    PREPARE = "prepare"
    COMMIT = "commit"


class TransportKind(StrEnum):
    INTERNAL = "internal"
    UNIX_PEER = "unix_peer"
    LOOPBACK_HTTP = "loopback_http"


class DecisionKind(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class PhasePolicy:
    phase: ExecutionPhase
    allowed_transports: frozenset[TransportKind]
    required_scopes: frozenset[str]
    approval_required: bool = False


@dataclass(frozen=True)
class CapabilityPolicy:
    capability_id: str
    risk: RiskLevel
    plan_approval_required: bool
    allowed_arguments: frozenset[str]
    required_arguments: frozenset[str]
    phases: tuple[PhasePolicy, ...]
    max_concurrency: int = 1
    timeout_seconds: float = 30.0

    def rule_for(self, phase: ExecutionPhase) -> PhasePolicy | None:
        return next((rule for rule in self.phases if rule.phase is phase), None)


@dataclass(frozen=True)
class TransportContext:
    transport: TransportKind
    principal: str
    peer_pid: int | None = None
    peer_uid: int | None = None
    peer_gid: int | None = None

    @classmethod
    def internal(cls) -> "TransportContext":
        return cls(TransportKind.INTERNAL, "core")


@dataclass(frozen=True)
class ExecutionContext:
    transport: TransportContext
    granted_scopes: frozenset[str]
    approval_granted: bool = False


@dataclass(frozen=True)
class CapabilityInvocation:
    request_id: str
    plan_id: str
    step_id: str
    capability_id: str
    phase: ExecutionPhase
    arguments: dict[str, JsonValue]
    declared_risk: str
    declared_approval_required: bool
    context: ExecutionContext
    deadline_monotonic: float | None = None
    trusted_payload: object | None = None


@dataclass(frozen=True)
class PermissionDecision:
    kind: DecisionKind
    capability_id: str
    phase: ExecutionPhase
    risk: RiskLevel | None
    reason_code: str
    required_scopes: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.kind is DecisionKind.ALLOW


@dataclass(frozen=True)
class DispatchResult:
    decision: PermissionDecision
    output: object | None = None
