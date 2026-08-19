"""Trusted first-party policies; module manifests cannot modify these values."""

from .contracts import (
    CapabilityPolicy,
    ExecutionPhase,
    PhasePolicy,
    RiskLevel,
    TransportKind,
)


ALL_LOCAL_TRANSPORTS = frozenset(
    {TransportKind.INTERNAL, TransportKind.UNIX_PEER, TransportKind.LOOPBACK_HTTP}
)
SECURE_WRITE_TRANSPORTS = frozenset(
    {TransportKind.INTERNAL, TransportKind.UNIX_PEER}
)


def builtin_policies() -> tuple[CapabilityPolicy, ...]:
    return (
        CapabilityPolicy(
            capability_id="documents.query.search",
            risk=RiskLevel.READ_ONLY,
            plan_approval_required=False,
            allowed_arguments=frozenset(
                {"text", "name_terms", "extensions", "volume_ids", "languages"}
            ),
            required_arguments=frozenset(),
            phases=(
                PhasePolicy(
                    ExecutionPhase.EXECUTE,
                    ALL_LOCAL_TRANSPORTS,
                    frozenset(
                        {"filesystem.read-metadata", "filesystem.read-content"}
                    ),
                ),
            ),
            max_concurrency=4,
            timeout_seconds=15,
        ),
        CapabilityPolicy(
            capability_id="storage.materialize.plan-copy",
            risk=RiskLevel.REVERSIBLE_WRITE,
            plan_approval_required=True,
            allowed_arguments=frozenset(
                {"results_from", "destination", "directory_name"}
            ),
            required_arguments=frozenset({"results_from", "destination"}),
            phases=(
                PhasePolicy(
                    ExecutionPhase.PREPARE,
                    ALL_LOCAL_TRANSPORTS,
                    frozenset(
                        {"filesystem.read-metadata", "filesystem.read-content"}
                    ),
                ),
                PhasePolicy(
                    ExecutionPhase.COMMIT,
                    SECURE_WRITE_TRANSPORTS,
                    frozenset(
                        {"filesystem.read-content", "filesystem.write-content"}
                    ),
                    approval_required=True,
                ),
            ),
            max_concurrency=1,
            timeout_seconds=300,
        ),
    )
