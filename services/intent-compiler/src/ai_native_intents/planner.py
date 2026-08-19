"""Deterministic intent-to-capability planning."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import uuid4

from .contracts import (
    CompilationState,
    ExecutionPlan,
    OperationKind,
    PlanStep,
    RiskClass,
    UserIntent,
)


_CAPABILITIES = {
    OperationKind.SEARCH_DOCUMENTS: "documents.query.search",
    OperationKind.FIND_APPLICATION: "desktop.applications.find",
    OperationKind.PLAN_WEB_SEARCH: "browser.search.plan",
    OperationKind.PLAN_OPEN_URL: "browser.url.plan",
    OperationKind.SAVE_RESULTS: "storage.collections.manage",
    OperationKind.COPY_RESULTS: "storage.materialize.plan-copy",
}

_RISKS = {
    OperationKind.COPY_RESULTS: RiskClass.REVERSIBLE_WRITE,
}


class IntentPlanner:
    """Maps validated semantics to declared capabilities without executing anything."""

    def plan(
        self,
        intent: UserIntent,
        *,
        available_capabilities: Iterable[str],
        clarification_question: str | None = None,
    ) -> ExecutionPlan:
        available = set(available_capabilities)
        steps: list[PlanStep] = []
        required: list[str] = []
        operation_ids = {operation.operation_id for operation in intent.operations}

        for operation in intent.operations:
            capability = _CAPABILITIES[operation.kind]
            required.append(capability)
            risk = _RISKS.get(operation.kind, RiskClass.READ_ONLY)
            reference_dependencies = {
                str(value)
                for value in operation.arguments.values()
                if isinstance(value, str) and value in operation_ids
            }
            dependencies = tuple(
                f"step_{dependency}"
                for dependency in dict.fromkeys((*operation.depends_on, *reference_dependencies))
            )
            steps.append(
                PlanStep(
                    step_id=f"step_{operation.operation_id}",
                    operation_id=operation.operation_id,
                    capability=capability,
                    arguments=dict(operation.arguments),
                    depends_on=dependencies,
                    risk=risk,
                    approval_required=risk is RiskClass.REVERSIBLE_WRITE,
                )
            )

        required_capabilities = tuple(dict.fromkeys(required))
        missing = tuple(capability for capability in required_capabilities if capability not in available)
        if clarification_question is not None:
            state = CompilationState.NEEDS_CLARIFICATION
        elif missing:
            state = CompilationState.UNAVAILABLE
        else:
            state = CompilationState.READY
        return ExecutionPlan(
            plan_id=str(uuid4()),
            intent_id=intent.intent_id,
            state=state,
            steps=tuple(steps),
            required_capabilities=required_capabilities,
            missing_capabilities=missing,
            approval_required=any(step.approval_required for step in steps),
            clarification_question=clarification_question,
        )
