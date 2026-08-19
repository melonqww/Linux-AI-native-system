"""Execution boundary for server-owned, deterministically validated plans."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid4

from ai_native_intents import CompilationState, ExecutionPlan, PlanStep, RiskClass, TaskContextStore
from ai_native_query import DocumentQuery, QueryService

from .contracts import (
    OrchestrationResult,
    OrchestrationState,
    SearchOutput,
    StepExecution,
    StepState,
)


AuditSink = Callable[[dict[str, object]], None]


class PlanValidationError(ValueError):
    pass


class ExecutionOrchestrator:
    """Executes the narrow v1 allowlist; it never dynamically invokes model names."""

    SEARCH_CAPABILITY = "documents.query.search"

    def __init__(
        self,
        query_service: QueryService,
        context_store: TaskContextStore,
        *,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._query_service = query_service
        self._context_store = context_store
        self._audit_sink = audit_sink

    def execute(self, plan: ExecutionPlan) -> OrchestrationResult:
        run_id = str(uuid4())
        try:
            self._validate(plan)
        except PlanValidationError:
            self._audit(
                "orchestration.rejected",
                run_id,
                getattr(plan, "plan_id", "invalid"),
                error_code="plan_validation_failed",
            )
            raise
        executions: list[StepExecution] = []
        completed: set[str] = set()
        active_collection_id: str | None = None
        self._audit("orchestration.started", run_id, plan.plan_id)

        for index, step in enumerate(plan.steps):
            if step.approval_required:
                pending = tuple(item.step_id for item in plan.steps[index:] if item.approval_required)
                executions.extend(
                    StepExecution(
                        item.step_id,
                        item.capability,
                        StepState.AWAITING_APPROVAL if item.approval_required else StepState.NOT_STARTED,
                    )
                    for item in plan.steps[index:]
                )
                self._audit("orchestration.awaiting_approval", run_id, plan.plan_id, step.step_id)
                return OrchestrationResult(
                    run_id,
                    plan.plan_id,
                    OrchestrationState.AWAITING_APPROVAL,
                    tuple(executions),
                    pending,
                    active_collection_id,
                )
            if not set(step.depends_on).issubset(completed):
                return self._failure(run_id, plan, executions, step, "dependency_not_completed")
            try:
                output = self._execute_search(step, plan.plan_id)
                active_collection_id = output.collection_id
                self._context_store.set_active_results(active_collection_id)
            except Exception as error:
                # This is the capability isolation boundary. Unexpected module
                # failures become a stable, redacted result and never tear down
                # the loopback request thread.
                return self._failure(
                    run_id, plan, executions, step, self._classify_error(error)
                )
            completed.add(step.step_id)
            executions.append(
                StepExecution(step.step_id, step.capability, StepState.COMPLETED, output=output)
            )
            self._audit(
                "orchestration.step_completed",
                run_id,
                plan.plan_id,
                step.step_id,
                result_count=output.result_count,
            )

        self._audit("orchestration.completed", run_id, plan.plan_id)
        return OrchestrationResult(
            run_id,
            plan.plan_id,
            OrchestrationState.COMPLETED,
            tuple(executions),
            active_collection_id=active_collection_id,
        )

    def _execute_search(self, step: PlanStep, plan_id: str) -> SearchOutput:
        arguments = step.arguments
        allowed = {"text", "name_terms", "extensions", "volume_ids", "languages"}
        unknown = set(arguments) - allowed
        if unknown:
            raise ValueError(f"unsupported search arguments: {sorted(unknown)}")
        text = self._string(arguments, "text")
        name_terms = self._strings(arguments, "name_terms")
        extensions = self._strings(arguments, "extensions")
        volume_ids = self._strings(arguments, "volume_ids")
        languages = self._strings(arguments, "languages")
        if not any((text.strip(), name_terms, extensions, volume_ids)):
            raise ValueError("search requires at least one criterion")
        results = self._query_service.search(
            DocumentQuery(
                text=text,
                name_contains=name_terms,
                extensions=extensions,
                volume_ids=volume_ids,
                limit=50,
            )
        )
        collection_id = self._query_service.save_snapshot(
            f"Search {plan_id[:8]}", results
        )
        warnings = ("language_filter_not_yet_applied",) if languages else ()
        return SearchOutput(collection_id, len(results), tuple(results), warnings)

    def _validate(self, plan: ExecutionPlan) -> None:
        if plan.state is not CompilationState.READY:
            raise PlanValidationError("plan_is_not_ready")
        try:
            UUID(plan.plan_id)
            UUID(plan.intent_id)
        except (ValueError, TypeError) as error:
            raise PlanValidationError("plan_identifiers_must_be_uuids") from error
        if not plan.steps or plan.missing_capabilities:
            raise PlanValidationError("plan_has_no_executable_steps")
        ids = [step.step_id for step in plan.steps]
        if len(ids) != len(set(ids)):
            raise PlanValidationError("duplicate_step_id")
        known: set[str] = set()
        required = tuple(dict.fromkeys(step.capability for step in plan.steps))
        if required != plan.required_capabilities:
            raise PlanValidationError("required_capabilities_mismatch")
        for step in plan.steps:
            if any(dependency not in known for dependency in step.depends_on):
                raise PlanValidationError("dependency_order_invalid")
            if step.capability == self.SEARCH_CAPABILITY:
                if step.risk is not RiskClass.READ_ONLY or step.approval_required:
                    raise PlanValidationError("search_risk_mismatch")
            elif step.capability == "storage.materialize.plan-copy":
                if step.risk is not RiskClass.REVERSIBLE_WRITE or not step.approval_required:
                    raise PlanValidationError("copy_risk_mismatch")
            else:
                raise PlanValidationError("capability_not_executable_in_v1")
            known.add(step.step_id)
        if plan.approval_required != any(step.approval_required for step in plan.steps):
            raise PlanValidationError("approval_flag_mismatch")

    def _failure(self, run_id, plan, executions, step, code) -> OrchestrationResult:
        executions.append(StepExecution(step.step_id, step.capability, StepState.FAILED, error_code=code))
        self._audit("orchestration.failed", run_id, plan.plan_id, step.step_id, error_code=code)
        return OrchestrationResult(
            run_id,
            plan.plan_id,
            OrchestrationState.FAILED,
            tuple(executions),
            diagnostics=(code,),
        )

    @staticmethod
    def _classify_error(error: Exception) -> str:
        if isinstance(error, TimeoutError):
            return "capability_timeout"
        if isinstance(error, OSError):
            return "storage_unavailable"
        if isinstance(error, ValueError):
            return "invalid_step_arguments"
        return "capability_internal_error"

    def _audit(self, event, run_id, plan_id, step_id=None, **metadata) -> None:
        if self._audit_sink is None:
            return
        payload: dict[str, object] = {"event": event, "run_id": run_id, "plan_id": plan_id}
        if step_id is not None:
            payload["step_id"] = step_id
        payload.update(metadata)
        self._audit_sink(payload)

    @staticmethod
    def _string(arguments, key) -> str:
        value = arguments.get(key, "")
        if not isinstance(value, str) or len(value) > 2_000:
            raise ValueError(f"{key} must be a bounded string")
        return value

    @staticmethod
    def _strings(arguments, key) -> tuple[str, ...]:
        value = arguments.get(key, ())
        if not isinstance(value, (tuple, list)) or len(value) > 100:
            raise ValueError(f"{key} must be a bounded string list")
        if any(not isinstance(item, str) or not item or len(item) > 255 for item in value):
            raise ValueError(f"{key} contains an invalid value")
        return tuple(value)
