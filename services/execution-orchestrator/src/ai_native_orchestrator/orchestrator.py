"""Execution boundary for server-owned, deterministically validated plans."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid4

from ai_native_intents import CompilationState, ExecutionPlan, PlanStep, RiskClass, TaskContextStore
from ai_native_query import DocumentQuery, QueryService
from ai_native_storage import (
    MaterializeDestinationError,
    MaterializeIntegrityError,
    MaterializeRollbackError,
    MaterializeService,
    MaterializeSpaceError,
)

from .approval import (
    ApprovalSession,
    ApprovalSessionError,
    ApprovalSessionStore,
    DestinationResolver,
)
from .contracts import (
    ApprovalRequest,
    CopyOutput,
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
        materialize_service: MaterializeService | None = None,
        destination_resolver: DestinationResolver | None = None,
        approval_sessions: ApprovalSessionStore | None = None,
    ) -> None:
        self._query_service = query_service
        self._context_store = context_store
        self._audit_sink = audit_sink
        self._materialize = materialize_service
        self._destinations = destination_resolver or DestinationResolver()
        self._approval_sessions = approval_sessions or ApprovalSessionStore()

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
        outputs_by_operation: dict[str, SearchOutput] = {}
        active_collection_id = self._context_store.snapshot().active_collection_id
        self._audit("orchestration.started", run_id, plan.plan_id)

        for index, step in enumerate(plan.steps):
            if not set(step.depends_on).issubset(completed):
                return self._failure(run_id, plan, executions, step, "dependency_not_completed")
            if step.approval_required:
                try:
                    approval = self._prepare_copy_approval(
                        run_id, plan, step, tuple(executions), outputs_by_operation,
                        active_collection_id,
                    )
                except Exception as error:
                    return self._failure(
                        run_id, plan, executions, step, self._classify_error(error)
                    )
                executions.append(
                    StepExecution(step.step_id, step.capability, StepState.AWAITING_APPROVAL)
                )
                self._audit("orchestration.awaiting_approval", run_id, plan.plan_id, step.step_id)
                return OrchestrationResult(
                    run_id,
                    plan.plan_id,
                    OrchestrationState.AWAITING_APPROVAL,
                    tuple(executions),
                    (step.step_id,),
                    active_collection_id,
                    approval_request=approval,
                )
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
            outputs_by_operation[step.operation_id] = output
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

    def respond_to_approval(
        self, approval_request_id: str, *, confirmed: bool
    ) -> OrchestrationResult:
        try:
            session, expired = self._approval_sessions.claim(approval_request_id)
        except ApprovalSessionError as error:
            self._discard_sessions(error.expired)
            raise
        self._discard_sessions(expired)
        step = session.step
        if self._materialize is None:
            raise RuntimeError("materialize_service_unavailable")
        if not confirmed:
            self._materialize.discard(session.materialize_plan.plan_id)
            execution = StepExecution(
                step.step_id, step.capability, StepState.CANCELLED, error_code="user_declined"
            )
            self._audit(
                "orchestration.cancelled", session.run_id, session.request.plan_id, step.step_id
            )
            return OrchestrationResult(
                session.run_id,
                session.request.plan_id,
                OrchestrationState.CANCELLED,
                (*session.prior_steps, execution),
                active_collection_id=session.active_collection_id,
                diagnostics=("user_declined",),
            )
        try:
            grant = self._materialize.approval.approve(
                session.materialize_plan.plan_id, user_confirmed=True
            )
            copied = self._materialize.execute(session.materialize_plan.plan_id, grant)
            self._context_store.set_last_destination(session.materialize_plan.destination)
        except Exception as error:
            self._materialize.discard(session.materialize_plan.plan_id)
            code = self._classify_error(error)
            execution = StepExecution(
                step.step_id, step.capability, StepState.FAILED, error_code=code
            )
            self._audit(
                "orchestration.failed",
                session.run_id,
                session.request.plan_id,
                step.step_id,
                error_code=code,
            )
            return OrchestrationResult(
                session.run_id,
                session.request.plan_id,
                OrchestrationState.FAILED,
                (*session.prior_steps, execution),
                active_collection_id=session.active_collection_id,
                diagnostics=(code,),
            )
        output = CopyOutput(
            session.materialize_plan.destination, len(copied), tuple(copied)
        )
        execution = StepExecution(step.step_id, step.capability, StepState.COMPLETED, output)
        self._audit(
            "orchestration.step_completed",
            session.run_id,
            session.request.plan_id,
            step.step_id,
            copied_count=len(copied),
        )
        self._audit("orchestration.completed", session.run_id, session.request.plan_id)
        return OrchestrationResult(
            session.run_id,
            session.request.plan_id,
            OrchestrationState.COMPLETED,
            (*session.prior_steps, execution),
            active_collection_id=session.active_collection_id,
        )

    def _prepare_copy_approval(
        self,
        run_id: str,
        plan: ExecutionPlan,
        step: PlanStep,
        prior_steps: tuple[StepExecution, ...],
        outputs: dict[str, SearchOutput],
        active_collection_id: str | None,
    ) -> ApprovalRequest:
        if self._materialize is None:
            raise RuntimeError("materialize service is unavailable")
        arguments = step.arguments
        unknown = set(arguments) - {"results_from", "destination", "directory_name"}
        if unknown:
            raise ValueError("copy has unsupported arguments")
        reference = self._string(arguments, "results_from")
        if reference in outputs:
            collection_id = outputs[reference].collection_id
        elif reference == self._context_store.snapshot().active_collection_id:
            collection_id = reference
        else:
            raise ValueError("copy results reference is not trusted")
        destination_role = self._string(arguments, "destination")
        raw_name = arguments.get("directory_name")
        if raw_name is not None and not isinstance(raw_name, str):
            raise ValueError("directory_name must be a string")
        context = self._context_store.snapshot()
        destination = self._destinations.resolve(
            destination_role,
            directory_name=raw_name,
            trusted_last_destination=context.last_destination,
        )
        materialize_plan = self._materialize.create_copy_plan(collection_id, destination)
        request = ApprovalRequest(
            approval_request_id=str(uuid4()),
            plan_id=plan.plan_id,
            step_id=step.step_id,
            action="copy_files",
            destination=materialize_plan.destination,
            item_count=len(materialize_plan.items),
            total_bytes=materialize_plan.total_bytes,
            item_names=tuple(item.destination_name for item in materialize_plan.items),
            expires_in_seconds=int(self._approval_sessions.ttl_seconds),
        )
        evicted = self._approval_sessions.put(
            ApprovalSession(
                run_id,
                request,
                materialize_plan,
                step,
                prior_steps,
                active_collection_id,
            )
        )
        self._discard_sessions(evicted)
        return request

    def _discard_sessions(self, sessions: tuple[ApprovalSession, ...]) -> None:
        if self._materialize is None:
            return
        for session in sessions:
            self._materialize.discard(session.materialize_plan.plan_id)

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
        operation_ids = [step.operation_id for step in plan.steps]
        if len(operation_ids) != len(set(operation_ids)):
            raise PlanValidationError("duplicate_operation_id")
        known: set[str] = set()
        required = tuple(dict.fromkeys(step.capability for step in plan.steps))
        if required != plan.required_capabilities:
            raise PlanValidationError("required_capabilities_mismatch")
        for index, step in enumerate(plan.steps):
            if step.step_id != f"step_{step.operation_id}":
                raise PlanValidationError("step_operation_identity_mismatch")
            if any(dependency not in known for dependency in step.depends_on):
                raise PlanValidationError("dependency_order_invalid")
            if step.capability == self.SEARCH_CAPABILITY:
                if step.risk is not RiskClass.READ_ONLY or step.approval_required:
                    raise PlanValidationError("search_risk_mismatch")
            elif step.capability == "storage.materialize.plan-copy":
                if step.risk is not RiskClass.REVERSIBLE_WRITE or not step.approval_required:
                    raise PlanValidationError("copy_risk_mismatch")
                if index != len(plan.steps) - 1:
                    raise PlanValidationError("copy_must_be_final_in_v1")
                reference = step.arguments.get("results_from")
                earlier_operations = set(operation_ids[:index])
                if reference in earlier_operations and f"step_{reference}" not in step.depends_on:
                    raise PlanValidationError("copy_result_dependency_missing")
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
        if isinstance(error, MaterializeIntegrityError):
            return "integrity_check_failed"
        if isinstance(error, MaterializeDestinationError):
            return "destination_changed"
        if isinstance(error, MaterializeSpaceError):
            return "insufficient_space"
        if isinstance(error, MaterializeRollbackError):
            return "rollback_incomplete"
        if isinstance(error, PermissionError):
            return "permission_denied"
        if isinstance(error, FileExistsError):
            return "destination_conflict"
        if isinstance(error, OSError):
            return "storage_unavailable"
        if isinstance(error, KeyError):
            return "resource_not_found"
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
