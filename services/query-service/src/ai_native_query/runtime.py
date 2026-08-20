"""Validated adapter between the loopback panel bridge and QueryService."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Callable, Protocol

from .contracts import DocumentQuery, QueryResult
from .service import QueryService
from ai_native_permissions import TransportContext


class IntentPipeline(Protocol):
    def compile_and_plan(self, text: str, *, context: object) -> object: ...


class PlanStore(Protocol):
    def put(self, plan: object) -> None: ...
    def claim(self, plan_id: str) -> object: ...


class PlanExecutor(Protocol):
    def available_capabilities(self) -> tuple[str, ...]: ...
    def execute(
        self, plan: object, *, transport_context: TransportContext | None = None
    ) -> object: ...
    def respond_to_approval(
        self,
        approval_request_id: str,
        *,
        confirmed: bool,
        transport_context: TransportContext | None = None,
    ) -> object: ...


class TaskHistory(Protocol):
    def list_recent(self, *, limit: int = 200) -> tuple[object, ...]: ...
    def get(self, task_id: str, *, include_references: bool = True) -> object: ...
    def request_cancel(self, task_id: str) -> object: ...
    def request_continue(self, task_id: str) -> object: ...


class WorkspaceHistory(Protocol):
    def list_messages(self, *, limit: int = 200) -> tuple[object, ...]: ...
    def list_runs(
        self, *, limit: int = 20, active_only: bool = False
    ) -> tuple[object, ...]: ...
    def run(self, run_id: str) -> object: ...


class QueryRuntimeApplication:
    def __init__(
        self,
        query_service: QueryService,
        *,
        scheduler_status: Callable[[], object] | None = None,
        system_monitor_status: Callable[[dict[str, object]], dict[str, object]] | None = None,
        system_updates_check: Callable[[dict[str, object]], dict[str, object]] | None = None,
        intent_pipeline: IntentPipeline | None = None,
        task_context: Callable[[], object] | None = None,
        plan_store: PlanStore | None = None,
        plan_executor: PlanExecutor | None = None,
        task_ledger: TaskHistory | None = None,
        workspace: WorkspaceHistory | None = None,
    ) -> None:
        self.query_service = query_service
        self.scheduler_status = scheduler_status
        self.system_monitor_status = system_monitor_status
        self.system_updates_check = system_updates_check
        self.intent_pipeline = intent_pipeline
        self.task_context = task_context
        self.plan_store = plan_store
        self.plan_executor = plan_executor
        self.task_ledger = task_ledger
        self.workspace = workspace
        if intent_pipeline is not None and task_context is None:
            raise ValueError("task_context is required with intent_pipeline")
        if (plan_store is None) != (plan_executor is None):
            raise ValueError("plan_store and plan_executor must be configured together")

    def capabilities(self) -> list[str]:
        capabilities = [
            "documents.query.search",
            "documents.pdf.extract",
            "storage.collection.snapshot",
        ]
        if self.intent_pipeline is not None:
            capabilities.append("intent.compile")
        if self.plan_executor is not None:
            capabilities.append("execution.plan.execute")
            if "storage.materialize.plan-copy" in self.plan_executor.available_capabilities():
                capabilities.append("execution.r1.copy")
        if self.task_ledger is not None:
            capabilities.extend(
                (
                    "tasks.activity.read",
                    "tasks.activity.detail",
                    "tasks.activity.control",
                )
            )
        if self.system_monitor_status is not None:
            capabilities.append("system.monitor.snapshot")
        if self.system_updates_check is not None:
            capabilities.append("system.updates.check")
        if self.workspace is not None:
            capabilities.extend(("workspace.messages.read", "workspace.runs.read"))
        return capabilities

    def workspace_messages(self, payload: dict[str, object]) -> tuple[object, ...]:
        if self.workspace is None:
            raise RuntimeError("workspace_unavailable")
        if set(payload) - {"limit"}:
            raise ValueError("unknown workspace message field")
        limit = payload.get("limit", 200)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("workspace message limit must be from 1 to 500")
        return self.workspace.list_messages(limit=limit)

    def workspace_run(self, payload: dict[str, object]) -> object:
        if self.workspace is None:
            raise RuntimeError("workspace_unavailable")
        if set(payload) != {"run_id"}:
            raise ValueError("exactly run_id is required")
        return self.workspace.run(self._string(payload, "run_id"))

    def workspace_runs(self, payload: dict[str, object]) -> tuple[object, ...]:
        if self.workspace is None:
            raise RuntimeError("workspace_unavailable")
        if set(payload) - {"limit", "active_only"}:
            raise ValueError("unknown workspace runs field")
        limit = payload.get("limit", 20)
        active_only = payload.get("active_only", False)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("workspace runs limit must be from 1 to 100")
        if not isinstance(active_only, bool):
            raise ValueError("active_only must be a boolean")
        return self.workspace.list_runs(limit=limit, active_only=active_only)

    def check_system_updates(self, payload: dict[str, object]) -> dict[str, object]:
        if self.system_updates_check is None:
            raise RuntimeError("system_updates_unavailable")
        if payload:
            raise ValueError("system update check does not accept fields")
        result = self.system_updates_check(payload)
        if not isinstance(result, dict):
            raise RuntimeError("system_updates_invalid_response")
        return result

    def system_status(
        self, payload: dict[str, object] | None = None
    ) -> dict[str, object]:
        if self.system_monitor_status is None:
            raise RuntimeError("system_monitor_unavailable")
        request = payload or {}
        if not isinstance(request, dict):
            raise ValueError("system monitor request must be an object")
        if set(request) - {"process_limit", "process_sort", "process_order"}:
            raise ValueError("unknown system monitor request field")
        process_limit = request.get("process_limit", 20)
        if (
            isinstance(process_limit, bool)
            or not isinstance(process_limit, int)
            or not 1 <= process_limit <= 50
        ):
            raise ValueError("process_limit must be an integer from 1 to 50")
        if request.get("process_sort", "cpu") not in {"cpu", "memory"}:
            raise ValueError("process_sort must be cpu or memory")
        if request.get("process_order", "desc") not in {"asc", "desc"}:
            raise ValueError("process_order must be asc or desc")
        status = self.system_monitor_status(request)
        if not isinstance(status, dict):
            raise RuntimeError("system_monitor_invalid_response")
        return status

    def tasks(self) -> tuple[object, ...]:
        if self.task_ledger is None:
            raise RuntimeError("task_ledger_unavailable")
        # Keep the single-frame Unix response comfortably below 64 KiB.
        # The panel currently renders only the first ten records.
        return self.task_ledger.list_recent(limit=50)

    def task_detail(self, payload: dict[str, object]) -> object:
        if self.task_ledger is None:
            raise RuntimeError("task_ledger_unavailable")
        self._only_task_id(payload)
        return self.task_ledger.get(self._string(payload, "task_id"))

    def cancel_task(self, payload: dict[str, object]) -> object:
        if self.task_ledger is None:
            raise RuntimeError("task_ledger_unavailable")
        self._only_task_id(payload)
        return self.task_ledger.request_cancel(self._string(payload, "task_id"))

    def continue_task(self, payload: dict[str, object]) -> object:
        if self.task_ledger is None:
            raise RuntimeError("task_ledger_unavailable")
        self._only_task_id(payload)
        return self.task_ledger.request_continue(self._string(payload, "task_id"))

    def search(self, payload: dict[str, object]) -> list[QueryResult]:
        text = self._string(payload, "text")
        name_contains = self._strings(payload, "name_contains")
        extensions = self._strings(payload, "extensions")
        volume_ids = self._strings(payload, "volume_ids")
        limit = payload.get("limit", 20)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        return self.query_service.search(
            DocumentQuery(
                text=text,
                name_contains=name_contains,
                extensions=extensions,
                volume_ids=volume_ids,
                limit=limit,
            )
        )

    def index_status(self) -> dict[str, object]:
        status: dict[str, object] = {
            "catalog": self.query_service.catalog.status(),
            "content_index": self.query_service.indexer.get_index_status(),
        }
        if self.scheduler_status is not None:
            scheduler = self.scheduler_status()
            status["scheduler"] = asdict(scheduler) if is_dataclass(scheduler) else scheduler
        return status

    def compile_intent(self, payload: dict[str, object]) -> object:
        if self.intent_pipeline is None:
            raise RuntimeError("intent_compiler_unavailable")
        unknown = set(payload) - {"text"}
        if unknown:
            raise ValueError(f"unknown fields: {sorted(unknown)}")
        text = self._string(payload, "text")
        if not text.strip():
            raise ValueError("text is required")
        if self.task_context is None:
            raise RuntimeError("intent_context_unavailable")
        result = self.intent_pipeline.compile_and_plan(text, context=self.task_context())
        plan = getattr(result, "plan", None)
        if plan is not None and self.plan_store is not None:
            self.plan_store.put(plan)
        return result

    def execute_plan(
        self,
        payload: dict[str, object],
        *,
        transport_context: TransportContext | None = None,
    ) -> object:
        if self.plan_store is None or self.plan_executor is None:
            raise RuntimeError("execution_orchestrator_unavailable")
        unknown = set(payload) - {"plan_id"}
        if unknown:
            raise ValueError(f"unknown fields: {sorted(unknown)}")
        plan_id = self._string(payload, "plan_id")
        plan = self.plan_store.claim(plan_id)
        return self.plan_executor.execute(plan, transport_context=transport_context)

    def respond_to_approval(
        self,
        payload: dict[str, object],
        *,
        transport_context: TransportContext | None = None,
    ) -> object:
        if self.plan_executor is None:
            raise RuntimeError("execution_orchestrator_unavailable")
        unknown = set(payload) - {"approval_request_id", "confirmed"}
        if unknown:
            raise ValueError(f"unknown fields: {sorted(unknown)}")
        request_id = self._string(payload, "approval_request_id")
        confirmed = payload.get("confirmed")
        if not isinstance(confirmed, bool):
            raise ValueError("confirmed must be a boolean")
        return self.plan_executor.respond_to_approval(
            request_id,
            confirmed=confirmed,
            transport_context=transport_context,
        )

    @staticmethod
    def _only_task_id(payload: dict[str, object]) -> None:
        if set(payload) != {"task_id"}:
            raise ValueError("exactly task_id is required")

    @staticmethod
    def _string(payload: dict[str, object], key: str) -> str:
        value = payload.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        if len(value) > 2_000:
            raise ValueError(f"{key} is too long")
        return value

    @staticmethod
    def _strings(payload: dict[str, object], key: str) -> tuple[str, ...]:
        value = payload.get(key, ())
        if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"{key} must be a list of strings")
        if len(value) > 100:
            raise ValueError(f"{key} has too many values")
        return tuple(value)
