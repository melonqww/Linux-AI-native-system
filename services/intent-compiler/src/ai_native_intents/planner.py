"""Deterministic intent-to-capability planning."""

from __future__ import annotations

from uuid import uuid4

from .catalog import OperationCatalog, OperationDefinition
from .contracts import (
    CompilationState,
    ExecutionPlan,
    PlanStep,
    UserIntent,
)


class IntentPlanner:
    """Maps validated semantics to declared capabilities without executing anything."""

    def plan(
        self,
        intent: UserIntent,
        *,
        operation_definitions: tuple[OperationDefinition, ...],
        clarification_question: str | None = None,
    ) -> ExecutionPlan:
        catalog = OperationCatalog(operation_definitions)
        steps: list[PlanStep] = []
        required: list[str] = []
        operation_ids = {operation.operation_id for operation in intent.operations}

        for operation in intent.operations:
            definition = catalog.operation(operation.kind)
            capability = definition.capability_id
            required.append(capability)
            reference_dependencies = {
                str(value)
                for value in operation.arguments.values()
                if isinstance(value, str) and value in operation_ids
            }
            dependencies = tuple(
                f"step_{dependency}"
                for dependency in dict.fromkeys(
                    (*operation.depends_on, *reference_dependencies)
                )
            )
            steps.append(
                PlanStep(
                    step_id=f"step_{operation.operation_id}",
                    operation_id=operation.operation_id,
                    capability=capability,
                    arguments=dict(operation.arguments),
                    depends_on=dependencies,
                    risk=definition.risk,
                    approval_required=definition.approval_required,
                )
            )

        required_capabilities = tuple(dict.fromkeys(required))
        if clarification_question is not None:
            state = CompilationState.NEEDS_CLARIFICATION
        else:
            state = CompilationState.READY
        return ExecutionPlan(
            plan_id=str(uuid4()),
            intent_id=intent.intent_id,
            state=state,
            steps=tuple(steps),
            required_capabilities=required_capabilities,
            missing_capabilities=(),
            approval_required=any(step.approval_required for step in steps),
            clarification_question=clarification_question,
        )
