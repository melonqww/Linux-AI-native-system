"""Append-only, redacted audit events for allowed tool requests."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .contracts import PolicyDecision


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    request_id: str
    timestamp: str
    intent: str
    tool: str
    risk_level: str
    decision: str
    result: str


def create_audit_event(*, intent: str, decision: PolicyDecision, result: str) -> AuditEvent:
    """Create a metadata-only event; request text and system data are not logged."""
    return AuditEvent(
        event_id=str(uuid4()),
        request_id=decision.request_id,
        timestamp=datetime.now(UTC).isoformat(),
        intent=intent,
        tool=decision.tool,
        risk_level=decision.risk_level.value,
        decision=decision.decision,
        result=result,
    )


class JsonlAuditLog:
    """Small append-only JSON Lines log suitable for the MVP demonstrator."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: AuditEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(asdict(event), ensure_ascii=False, sort_keys=True))
            stream.write("\n")
