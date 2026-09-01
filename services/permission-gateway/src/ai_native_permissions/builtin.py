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
                {
                    "mode",
                    "text",
                    "name_terms",
                    "extensions",
                    "volume_ids",
                    "languages",
                }
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
        CapabilityPolicy(
            capability_id="software.install.prepare",
            risk=RiskLevel.REVERSIBLE_WRITE,
            plan_approval_required=True,
            allowed_arguments=frozenset(
                {"application_id", "locale", "install_location", "selected_options"}
            ),
            required_arguments=frozenset({"application_id"}),
            phases=(
                PhasePolicy(
                    ExecutionPhase.PREPARE,
                    SECURE_WRITE_TRANSPORTS,
                    frozenset({"software.manage"}),
                ),
            ),
            timeout_seconds=15,
        ),
        CapabilityPolicy(
            capability_id="software.install.commit",
            risk=RiskLevel.PRIVILEGED,
            plan_approval_required=True,
            allowed_arguments=frozenset({"task_id"}),
            required_arguments=frozenset({"task_id"}),
            phases=(
                PhasePolicy(
                    ExecutionPhase.COMMIT,
                    SECURE_WRITE_TRANSPORTS,
                    frozenset({"software.manage"}),
                    approval_required=True,
                ),
            ),
            timeout_seconds=60,
        ),
        CapabilityPolicy(
            capability_id="software.remove.prepare",
            risk=RiskLevel.REVERSIBLE_WRITE,
            plan_approval_required=True,
            allowed_arguments=frozenset({"application_id", "create_backup"}),
            required_arguments=frozenset({"application_id", "create_backup"}),
            phases=(
                PhasePolicy(
                    ExecutionPhase.PREPARE,
                    SECURE_WRITE_TRANSPORTS,
                    frozenset({"software.manage"}),
                ),
            ),
            timeout_seconds=15,
        ),
        CapabilityPolicy(
            capability_id="software.remove.commit",
            risk=RiskLevel.PRIVILEGED,
            plan_approval_required=True,
            allowed_arguments=frozenset({"task_id", "final_confirmation"}),
            required_arguments=frozenset({"task_id", "final_confirmation"}),
            phases=(
                PhasePolicy(
                    ExecutionPhase.COMMIT,
                    SECURE_WRITE_TRANSPORTS,
                    frozenset({"software.manage"}),
                    approval_required=True,
                ),
            ),
            timeout_seconds=60,
        ),
        CapabilityPolicy(
            capability_id="software.tasks.control",
            risk=RiskLevel.REVERSIBLE_WRITE,
            plan_approval_required=False,
            allowed_arguments=frozenset({"task_id", "action"}),
            required_arguments=frozenset({"task_id", "action"}),
            phases=(
                PhasePolicy(
                    ExecutionPhase.EXECUTE,
                    SECURE_WRITE_TRANSPORTS,
                    frozenset({"software.manage"}),
                ),
            ),
            timeout_seconds=60,
        ),
        CapabilityPolicy(
            capability_id="software.backup.restore",
            risk=RiskLevel.PRIVILEGED,
            plan_approval_required=True,
            allowed_arguments=frozenset({"backup_id"}),
            required_arguments=frozenset({"backup_id"}),
            phases=(
                PhasePolicy(
                    ExecutionPhase.COMMIT,
                    SECURE_WRITE_TRANSPORTS,
                    frozenset({"software.manage"}),
                    approval_required=True,
                ),
            ),
            timeout_seconds=60,
        ),
    )
