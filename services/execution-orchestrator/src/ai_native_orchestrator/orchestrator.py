"""Execution boundary for server-owned, deterministically validated plans."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from time import monotonic
from uuid import UUID, uuid4

from ai_native_intents import CompilationState, ExecutionPlan, PlanStep, TaskContextStore
from ai_native_ledger import CancellationRequested, ItemOutcome, TaskLedger
from ai_native_query import DocumentQuery, QueryService
from ai_native_permissions import (
    CapabilityExecutionRegistry,
    CapabilityInvocation,
    ExecutionContext,
    ExecutionPhase,
    PermissionGateway,
    ScopeGrantStore,
    TransportContext,
    builtin_policies,
)
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


@dataclass(frozen=True)
class _CopyPreparePayload:
    run_id: str
    plan: ExecutionPlan
    step: PlanStep
    prior_steps: tuple[StepExecution, ...]
    outputs: dict[str, SearchOutput]
    active_collection_id: str | None


@dataclass(frozen=True)
class _CopyCommitPayload:
    session: ApprovalSession


class ExecutionOrchestrator:
    """Executes plans only through trusted policy and handler registries."""

    def __init__(
        self,
        query_service: QueryService,
        context_store: TaskContextStore,
        *,
        audit_sink: AuditSink | None = None,
        materialize_service: MaterializeService | None = None,
        destination_resolver: DestinationResolver | None = None,
        approval_sessions: ApprovalSessionStore | None = None,
        capability_source: Callable[[], Iterable[str]] | None = None,
        granted_scopes: frozenset[str] | None = None,
        scope_source: Callable[[], Iterable[str]] | None = None,
        task_ledger: TaskLedger | None = None,
    ) -> None:
        self._query_service = query_service
        self._context_store = context_store
        self._audit_sink = audit_sink
        self._materialize = materialize_service
        self._destinations = destination_resolver or DestinationResolver()
        self._approval_sessions = approval_sessions or ApprovalSessionStore()
        self._task_ledger = task_ledger
        initial_scopes = (
            frozenset(
                {
                    "filesystem.read-metadata",
                    "filesystem.read-content",
                    "filesystem.write-content",
                }
            )
            if granted_scopes is None
            else granted_scopes
        )
        self.scope_grants = ScopeGrantStore(initial_scopes)
        self._scope_source = scope_source or self.scope_grants.snapshot
        self.permission_gateway = PermissionGateway(
            builtin_policies(), capability_source=capability_source
        )
        self.capabilities = CapabilityExecutionRegistry(self.permission_gateway)
        self.capabilities.register("documents.query.search", self._handle_search)
        self.capabilities.register("storage.materialize.plan-copy", self._handle_copy)

    def available_capabilities(self) -> tuple[str, ...]:
        registered = set(self.capabilities.registered_capabilities())
        return tuple(
            capability
            for capability in self.permission_gateway.available_capabilities()
            if capability in registered
        )

    def execute(
        self,
        plan: ExecutionPlan,
        *,
        transport_context: TransportContext | None = None,
    ) -> OrchestrationResult:
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
        transport = transport_context or TransportContext.internal()
        self._audit("orchestration.started", run_id, plan.plan_id)
        self._ledger_start(run_id, plan)

        for index, step in enumerate(plan.steps):
            if self._task_ledger is not None:
                try:
                    self._task_ledger.raise_if_cancelled(run_id)
                except CancellationRequested:
                    return self._cancelled(run_id, plan, executions, step)
            if not set(step.depends_on).issubset(completed):
                return self._failure(run_id, plan, executions, step, "dependency_not_completed")
            if step.approval_required:
                try:
                    invocation = self._invocation(
                        plan,
                        step,
                        ExecutionPhase.PREPARE,
                        transport,
                        trusted_payload=_CopyPreparePayload(
                            run_id,
                            plan,
                            step,
                            tuple(executions),
                            dict(outputs_by_operation),
                            active_collection_id,
                        ),
                    )
                    dispatch = self.capabilities.dispatch(
                        invocation,
                        decision_observer=lambda decision: self._audit_permission(
                            run_id, plan.plan_id, step, invocation, decision
                        ),
                    )
                    if not dispatch.decision.allowed:
                        return self._failure(
                            run_id,
                            plan,
                            executions,
                            step,
                            f"policy_{dispatch.decision.reason_code}",
                        )
                    if not isinstance(dispatch.output, ApprovalRequest):
                        raise TypeError("R1 prepare handler returned an invalid output")
                    approval = dispatch.output
                except Exception as error:
                    return self._failure(
                        run_id, plan, executions, step, self._classify_error(error)
                    )
                executions.append(
                    StepExecution(step.step_id, step.capability, StepState.AWAITING_APPROVAL)
                )
                self._audit("orchestration.awaiting_approval", run_id, plan.plan_id, step.step_id)
                if self._task_ledger is not None:
                    self._task_ledger.awaiting_approval(run_id)
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
                invocation = self._invocation(
                    plan, step, ExecutionPhase.EXECUTE, transport
                )
                dispatch = self.capabilities.dispatch(
                    invocation,
                    decision_observer=lambda decision: self._audit_permission(
                        run_id, plan.plan_id, step, invocation, decision
                    ),
                )
                if not dispatch.decision.allowed:
                    return self._failure(
                        run_id,
                        plan,
                        executions,
                        step,
                        f"policy_{dispatch.decision.reason_code}",
                    )
                if not isinstance(dispatch.output, SearchOutput):
                    raise TypeError("R0 handler returned an invalid output")
                output = dispatch.output
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
            self._ledger_search_results(run_id, plan, output)
            self._audit(
                "orchestration.step_completed",
                run_id,
                plan.plan_id,
                step.step_id,
                result_count=output.result_count,
            )

        self._audit("orchestration.completed", run_id, plan.plan_id)
        if self._task_ledger is not None:
            self._task_ledger.complete(run_id)
        return OrchestrationResult(
            run_id,
            plan.plan_id,
            OrchestrationState.COMPLETED,
            tuple(executions),
            active_collection_id=active_collection_id,
        )

    def respond_to_approval(
        self,
        approval_request_id: str,
        *,
        confirmed: bool,
        transport_context: TransportContext | None = None,
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
            if self._task_ledger is not None:
                self._task_ledger.cancelled(session.run_id)
            return OrchestrationResult(
                session.run_id,
                session.request.plan_id,
                OrchestrationState.CANCELLED,
                (*session.prior_steps, execution),
                active_collection_id=session.active_collection_id,
                diagnostics=("user_declined",),
            )
        try:
            if self._task_ledger is not None:
                self._task_ledger.approval_received(session.run_id)
            invocation = self._invocation(
                None,
                step,
                ExecutionPhase.COMMIT,
                transport_context or TransportContext.internal(),
                approval_granted=True,
                plan_id=session.request.plan_id,
                trusted_payload=_CopyCommitPayload(session),
            )
            dispatch = self.capabilities.dispatch(
                invocation,
                decision_observer=lambda decision: self._audit_permission(
                    session.run_id,
                    session.request.plan_id,
                    step,
                    invocation,
                    decision,
                ),
            )
            if not dispatch.decision.allowed:
                self._materialize.discard(session.materialize_plan.plan_id)
                code = f"policy_{dispatch.decision.reason_code}"
                execution = StepExecution(
                    step.step_id, step.capability, StepState.FAILED, error_code=code
                )
                self._audit(
                    "orchestration.denied",
                    session.run_id,
                    session.request.plan_id,
                    step.step_id,
                    error_code=code,
                )
                if self._task_ledger is not None:
                    self._task_ledger.fail(session.run_id)
                return OrchestrationResult(
                    session.run_id,
                    session.request.plan_id,
                    OrchestrationState.FAILED,
                    (*session.prior_steps, execution),
                    active_collection_id=session.active_collection_id,
                    diagnostics=(code,),
                )
            if not isinstance(dispatch.output, CopyOutput):
                raise TypeError("R1 commit handler returned an invalid output")
            output = dispatch.output
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
            if self._task_ledger is not None:
                self._task_ledger.fail(session.run_id)
            return OrchestrationResult(
                session.run_id,
                session.request.plan_id,
                OrchestrationState.FAILED,
                (*session.prior_steps, execution),
                active_collection_id=session.active_collection_id,
                diagnostics=(code,),
            )
        execution = StepExecution(step.step_id, step.capability, StepState.COMPLETED, output)
        if self._task_ledger is not None:
            for copied_path in output.copied_paths:
                self._task_ledger.record_item(
                    session.run_id,
                    outcome=ItemOutcome.SUCCEEDED,
                    locator=copied_path,
                    kind="copied-file",
                )
        self._audit(
            "orchestration.step_completed",
            session.run_id,
            session.request.plan_id,
            step.step_id,
            copied_count=output.copied_count,
        )
        self._audit("orchestration.completed", session.run_id, session.request.plan_id)
        if self._task_ledger is not None:
            self._task_ledger.complete(session.run_id)
        return OrchestrationResult(
            session.run_id,
            session.request.plan_id,
            OrchestrationState.COMPLETED,
            (*session.prior_steps, execution),
            active_collection_id=session.active_collection_id,
        )

    def _invocation(
        self,
        plan: ExecutionPlan | None,
        step: PlanStep,
        phase: ExecutionPhase,
        transport: TransportContext,
        *,
        approval_granted: bool = False,
        plan_id: str | None = None,
        trusted_payload: object | None = None,
    ) -> CapabilityInvocation:
        resolved_plan_id = plan.plan_id if plan is not None else plan_id
        if resolved_plan_id is None:
            raise ValueError("plan_id is required for capability invocation")
        return CapabilityInvocation(
            request_id=str(uuid4()),
            plan_id=resolved_plan_id,
            step_id=step.step_id,
            capability_id=step.capability,
            phase=phase,
            arguments=dict(step.arguments),
            declared_risk=step.risk.value,
            declared_approval_required=step.approval_required,
            context=ExecutionContext(
                transport,
                frozenset(self._scope_source()),
                approval_granted=approval_granted,
            ),
            trusted_payload=trusted_payload,
        )

    def _handle_search(self, invocation: CapabilityInvocation) -> SearchOutput:
        self._ensure_deadline(invocation)
        if invocation.phase is not ExecutionPhase.EXECUTE:
            raise ValueError("search handler only supports execute")
        return self._execute_search(
            invocation.arguments, invocation.plan_id, invocation.deadline_monotonic
        )

    def _handle_copy(self, invocation: CapabilityInvocation) -> ApprovalRequest | CopyOutput:
        self._ensure_deadline(invocation)
        if invocation.phase is ExecutionPhase.PREPARE:
            payload = invocation.trusted_payload
            if not isinstance(payload, _CopyPreparePayload):
                raise TypeError("copy prepare requires trusted orchestration state")
            return self._prepare_copy_approval(
                payload.run_id,
                payload.plan,
                payload.step,
                payload.prior_steps,
                payload.outputs,
                payload.active_collection_id,
                deadline_monotonic=invocation.deadline_monotonic,
            )
        if invocation.phase is ExecutionPhase.COMMIT:
            payload = invocation.trusted_payload
            if not isinstance(payload, _CopyCommitPayload):
                raise TypeError("copy commit requires trusted approval state")
            if self._materialize is None:
                raise RuntimeError("materialize_service_unavailable")
            session = payload.session
            grant = self._materialize.approval.approve(
                session.materialize_plan.plan_id, user_confirmed=True
            )
            copied = self._materialize.execute(
                session.materialize_plan.plan_id,
                grant,
                deadline_monotonic=invocation.deadline_monotonic,
                cancellation_check=self._cancellation_check(session.run_id),
            )
            self._context_store.set_last_destination(session.materialize_plan.destination)
            return CopyOutput(
                session.materialize_plan.destination, len(copied), tuple(copied)
            )
        raise ValueError("copy handler does not support this phase")

    @staticmethod
    def _ensure_deadline(invocation: CapabilityInvocation) -> None:
        if (
            invocation.deadline_monotonic is not None
            and monotonic() >= invocation.deadline_monotonic
        ):
            raise TimeoutError("capability deadline expired before execution")

    def _prepare_copy_approval(
        self,
        run_id: str,
        plan: ExecutionPlan,
        step: PlanStep,
        prior_steps: tuple[StepExecution, ...],
        outputs: dict[str, SearchOutput],
        active_collection_id: str | None,
        *,
        deadline_monotonic: float | None = None,
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
        materialize_plan = self._materialize.create_copy_plan(
            collection_id,
            destination,
            deadline_monotonic=deadline_monotonic,
            cancellation_check=self._cancellation_check(run_id),
        )
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
            if self._task_ledger is not None:
                self._task_ledger.fail(session.run_id)

    def _execute_search(
        self,
        arguments: dict[str, object],
        plan_id: str,
        deadline_monotonic: float | None,
    ) -> SearchOutput:
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
        if deadline_monotonic is not None and monotonic() >= deadline_monotonic:
            raise TimeoutError("search deadline expired before snapshot")
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
            try:
                policy = self.permission_gateway.policy(step.capability)
            except ValueError as error:
                raise PlanValidationError("capability_has_no_trusted_policy") from error
            if step.risk.value != policy.risk.value:
                raise PlanValidationError("step_risk_mismatch")
            if step.approval_required != policy.plan_approval_required:
                raise PlanValidationError("step_approval_mismatch")
            phase = (
                ExecutionPhase.PREPARE
                if step.approval_required
                else ExecutionPhase.EXECUTE
            )
            if policy.rule_for(phase) is None:
                raise PlanValidationError("capability_phase_not_supported")
            if step.approval_required and index != len(plan.steps) - 1:
                raise PlanValidationError("approval_step_must_be_final_in_v1")
            reference = step.arguments.get("results_from")
            earlier_operations = set(operation_ids[:index])
            if reference in earlier_operations and f"step_{reference}" not in step.depends_on:
                raise PlanValidationError("result_dependency_missing")
            known.add(step.step_id)
        if plan.approval_required != any(step.approval_required for step in plan.steps):
            raise PlanValidationError("approval_flag_mismatch")

    def _failure(self, run_id, plan, executions, step, code) -> OrchestrationResult:
        if code == "user_cancelled":
            return self._cancelled(run_id, plan, executions, step)
        executions.append(StepExecution(step.step_id, step.capability, StepState.FAILED, error_code=code))
        self._audit("orchestration.failed", run_id, plan.plan_id, step.step_id, error_code=code)
        if self._task_ledger is not None:
            self._task_ledger.fail(run_id)
        return OrchestrationResult(
            run_id,
            plan.plan_id,
            OrchestrationState.FAILED,
            tuple(executions),
            diagnostics=(code,),
        )

    def _cancelled(self, run_id, plan, executions, step) -> OrchestrationResult:
        executions.append(
            StepExecution(
                step.step_id,
                step.capability,
                StepState.CANCELLED,
                error_code="user_cancelled",
            )
        )
        self._audit("orchestration.cancelled", run_id, plan.plan_id, step.step_id)
        if self._task_ledger is not None:
            self._task_ledger.cancelled(run_id)
        return OrchestrationResult(
            run_id,
            plan.plan_id,
            OrchestrationState.CANCELLED,
            tuple(executions),
        )

    def _ledger_start(self, run_id: str, plan: ExecutionPlan) -> None:
        if self._task_ledger is None:
            return
        activity = (
            "files.copy"
            if any(step.capability == "storage.materialize.plan-copy" for step in plan.steps)
            else "documents.search"
        )
        self._task_ledger.create(activity, task_id=run_id)
        self._task_ledger.start(run_id)

    def _ledger_search_results(
        self, run_id: str, plan: ExecutionPlan, output: SearchOutput
    ) -> None:
        if self._task_ledger is None:
            return
        has_copy = any(
            step.capability == "storage.materialize.plan-copy" for step in plan.steps
        )
        for result in output.results:
            arguments = {
                "locator": result.path,
                "display_name": result.name,
                "kind": "found-file",
                "outcome": ItemOutcome.SUCCEEDED,
            }
            if has_copy:
                self._task_ledger.add_reference(run_id, **arguments)
            else:
                self._task_ledger.record_item(run_id, **arguments)

    def _cancellation_check(self, run_id: str):
        if self._task_ledger is None:
            return None
        return lambda: self._task_ledger.raise_if_cancelled(run_id)

    @staticmethod
    def _classify_error(error: Exception) -> str:
        if isinstance(error, CancellationRequested):
            return "user_cancelled"
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

    def _audit_permission(
        self, run_id, plan_id, step, invocation, decision
    ) -> None:
        self._audit(
            "permission.decision",
            run_id,
            plan_id,
            step.step_id,
            capability=step.capability,
            phase=invocation.phase.value,
            transport=invocation.context.transport.transport.value,
            risk=None if decision.risk is None else decision.risk.value,
            decision=decision.kind.value,
            reason_code=decision.reason_code,
        )

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
