"""Small, strict allowlist for the first safe system tool."""

from collections.abc import Mapping
from uuid import UUID

from .contracts import IntentProposal, PolicyDecision, RiskLevel, SystemMetric


class PolicyViolation(ValueError):
    """Raised when a proposal is not part of the allowed tool contract."""


class PolicyEngine:
    """Validates model output before it reaches any OS-facing code."""

    _EXPECTED_FIELDS = frozenset({"request_id", "intent", "arguments", "reason"})
    _EXPECTED_ARGUMENTS = frozenset({"metrics", "process_limit"})
    _INTENT = "get_system_status"
    _TOOL = "ubuntu.system.status.read"

    def evaluate(self, payload: Mapping[str, object]) -> tuple[IntentProposal, PolicyDecision]:
        """Return an R0 allow decision or reject the untrusted proposal."""
        self._require_exact_fields(payload, self._EXPECTED_FIELDS, "proposal")

        request_id = self._validate_uuid(payload["request_id"])
        if payload["intent"] != self._INTENT:
            raise PolicyViolation("intent is not allowlisted")

        reason = payload["reason"]
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise PolicyViolation("reason must be a non-empty string up to 500 characters")

        arguments = payload["arguments"]
        if not isinstance(arguments, Mapping):
            raise PolicyViolation("arguments must be an object")
        self._require_exact_fields(arguments, self._EXPECTED_ARGUMENTS, "arguments")

        metrics = self._validate_metrics(arguments["metrics"])
        process_limit = self._validate_process_limit(arguments["process_limit"])
        proposal = IntentProposal(
            request_id=request_id,
            intent=self._INTENT,
            metrics=metrics,
            process_limit=process_limit,
            reason=reason.strip(),
        )
        decision = PolicyDecision(
            request_id=request_id,
            tool=self._TOOL,
            risk_level=RiskLevel.READ_ONLY,
            decision="allow",
            approval_required=False,
            allowed_scopes=("current-user", "host-metrics"),
        )
        return proposal, decision

    @staticmethod
    def _require_exact_fields(value: Mapping[str, object], expected: frozenset[str], label: str) -> None:
        if set(value) != expected:
            raise PolicyViolation(f"{label} fields do not match the contract")

    @staticmethod
    def _validate_uuid(value: object) -> str:
        if not isinstance(value, str):
            raise PolicyViolation("request_id must be a UUID string")
        try:
            return str(UUID(value))
        except ValueError as error:
            raise PolicyViolation("request_id must be a UUID string") from error

    @staticmethod
    def _validate_metrics(value: object) -> tuple[SystemMetric, ...]:
        if not isinstance(value, list) or not value:
            raise PolicyViolation("metrics must be a non-empty list")
        try:
            metrics = tuple(SystemMetric(metric) for metric in value)
        except ValueError as error:
            raise PolicyViolation("metrics contains a non-allowlisted value") from error
        if len(set(metrics)) != len(metrics):
            raise PolicyViolation("metrics must not contain duplicates")
        return metrics

    @staticmethod
    def _validate_process_limit(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 20:
            raise PolicyViolation("process_limit must be an integer from 1 to 20")
        return value
