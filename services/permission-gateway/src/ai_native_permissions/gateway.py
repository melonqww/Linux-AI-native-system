"""Deterministic system-owned policy evaluation for capability invocations."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from threading import RLock
from uuid import UUID

from .contracts import (
    CapabilityInvocation,
    CapabilityPolicy,
    DecisionKind,
    PermissionDecision,
)


_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")
_SCOPE_ID = _CAPABILITY_ID


class PolicyConfigurationError(ValueError):
    pass


class ScopeGrantStore:
    """Trusted, thread-safe grants; manifests may request but never mutate them."""

    def __init__(self, scopes: Iterable[str] = ()) -> None:
        self._lock = RLock()
        self._scopes: frozenset[str] = frozenset()
        self.replace(scopes)

    def snapshot(self) -> frozenset[str]:
        with self._lock:
            return self._scopes

    def replace(self, scopes: Iterable[str]) -> frozenset[str]:
        validated = self._validate(scopes)
        with self._lock:
            self._scopes = validated
            return self._scopes

    def grant(self, *scopes: str) -> frozenset[str]:
        additions = self._validate(scopes)
        with self._lock:
            self._scopes = self._scopes | additions
            return self._scopes

    def revoke(self, *scopes: str) -> frozenset[str]:
        removals = self._validate(scopes)
        with self._lock:
            self._scopes = self._scopes - removals
            return self._scopes

    @staticmethod
    def _validate(scopes: Iterable[str]) -> frozenset[str]:
        values = frozenset(scopes)
        if any(not isinstance(scope, str) or not _SCOPE_ID.fullmatch(scope) for scope in values):
            raise ValueError("invalid permission scope")
        return values


class PermissionGateway:
    def __init__(
        self,
        policies: Iterable[CapabilityPolicy],
        *,
        capability_source: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        configured: dict[str, CapabilityPolicy] = {}
        for policy in policies:
            self._validate_policy(policy)
            if policy.capability_id in configured:
                raise PolicyConfigurationError("duplicate capability policy")
            configured[policy.capability_id] = policy
        if not configured:
            raise PolicyConfigurationError("at least one capability policy is required")
        self._policies = configured
        self._capability_source = capability_source or configured.keys

    def policy(self, capability_id: str) -> CapabilityPolicy:
        try:
            return self._policies[capability_id]
        except KeyError as error:
            raise PolicyConfigurationError("capability has no trusted policy") from error

    def available_capabilities(self) -> tuple[str, ...]:
        available = set(self._capability_source())
        return tuple(sorted(available & self._policies.keys()))

    def required_scopes(self, capability_id: str) -> frozenset[str]:
        policy = self.policy(capability_id)
        return frozenset().union(*(rule.required_scopes for rule in policy.phases))

    def evaluate(self, invocation: CapabilityInvocation) -> PermissionDecision:
        if not self._valid_invocation_identity(invocation):
            return self.denied(invocation, None, "invalid_invocation_identity")
        policy = self._policies.get(invocation.capability_id)
        if policy is None:
            return self.denied(invocation, None, "policy_not_found")
        if invocation.capability_id not in set(self._capability_source()):
            return self.denied(invocation, policy, "capability_unavailable")
        if invocation.declared_risk != policy.risk.value:
            return self.denied(invocation, policy, "declared_risk_mismatch")
        if invocation.declared_approval_required != policy.plan_approval_required:
            return self.denied(invocation, policy, "declared_approval_mismatch")
        rule = policy.rule_for(invocation.phase)
        if rule is None:
            return self.denied(invocation, policy, "phase_not_allowed")
        if invocation.context.transport.transport not in rule.allowed_transports:
            return self.denied(invocation, policy, "transport_not_allowed")
        if rule.approval_required and not invocation.context.approval_granted:
            return self.denied(invocation, policy, "approval_required")
        missing = tuple(sorted(rule.required_scopes - invocation.context.granted_scopes))
        if missing:
            return PermissionDecision(
                DecisionKind.DENY,
                invocation.capability_id,
                invocation.phase,
                policy.risk,
                "scope_not_granted",
                missing,
            )
        keys = set(invocation.arguments)
        if not policy.required_arguments.issubset(keys):
            return self.denied(invocation, policy, "required_arguments_missing")
        if not keys.issubset(policy.allowed_arguments):
            return self.denied(invocation, policy, "arguments_not_allowed")
        if not self._valid_arguments(invocation.arguments):
            return self.denied(invocation, policy, "invalid_argument_value")
        return PermissionDecision(
            DecisionKind.ALLOW,
            invocation.capability_id,
            invocation.phase,
            policy.risk,
            "allowed",
        )

    @staticmethod
    def denied(invocation, policy, reason) -> PermissionDecision:
        return PermissionDecision(
            DecisionKind.DENY,
            invocation.capability_id,
            invocation.phase,
            None if policy is None else policy.risk,
            reason,
        )

    @staticmethod
    def _valid_invocation_identity(invocation: CapabilityInvocation) -> bool:
        try:
            return (
                str(UUID(invocation.request_id)) == invocation.request_id
                and str(UUID(invocation.plan_id)) == invocation.plan_id
                and bool(re.fullmatch(r"step_[a-z][a-z0-9_]{0,63}", invocation.step_id))
                and _CAPABILITY_ID.fullmatch(invocation.capability_id) is not None
            )
        except (ValueError, TypeError, AttributeError):
            return False

    @staticmethod
    def _valid_arguments(arguments: dict[str, object]) -> bool:
        if not isinstance(arguments, dict) or len(arguments) > 32:
            return False
        for value in arguments.values():
            if isinstance(value, str):
                if not value or len(value) > 4_096 or any(ord(char) < 32 for char in value):
                    return False
            elif isinstance(value, tuple):
                if len(value) > 100 or any(
                    not isinstance(item, str)
                    or not item
                    or len(item) > 300
                    or any(ord(char) < 32 for char in item)
                    for item in value
                ):
                    return False
            elif value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                return False
        return True

    @staticmethod
    def _validate_policy(policy: CapabilityPolicy) -> None:
        if not _CAPABILITY_ID.fullmatch(policy.capability_id):
            raise PolicyConfigurationError("invalid capability ID in policy")
        if not policy.phases or len({rule.phase for rule in policy.phases}) != len(policy.phases):
            raise PolicyConfigurationError("policy phases must be non-empty and unique")
        if policy.max_concurrency < 1 or policy.timeout_seconds <= 0:
            raise PolicyConfigurationError("policy resource limits must be positive")
        if not policy.required_arguments.issubset(policy.allowed_arguments):
            raise PolicyConfigurationError("required arguments must be allowed")
        for value in (*policy.allowed_arguments, *policy.required_arguments):
            if not isinstance(value, str) or not value or len(value) > 64:
                raise PolicyConfigurationError("invalid argument name in policy")
        for rule in policy.phases:
            if not rule.allowed_transports:
                raise PolicyConfigurationError("phase must allow at least one transport")
            if any(not _SCOPE_ID.fullmatch(scope) for scope in rule.required_scopes):
                raise PolicyConfigurationError("invalid scope ID in policy")
