"""Validated adapter between the loopback panel bridge and QueryService."""

from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from typing import Callable, Protocol
from uuid import uuid4

from .contracts import ContentMatch, DocumentQuery, QueryResult, SearchMode, SearchPage
from .service import QueryService
from ai_native_permissions import (
    CapabilityInvocation,
    ExecutionContext,
    ExecutionPhase,
    PermissionGateway,
    TransportContext,
    builtin_policies,
)
from ai_native_storage import PermissionLevel


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


class WorkspaceController(Protocol):
    def submit(self, text: str, *, transport_context: TransportContext) -> object: ...

    def respond_to_approval(
        self,
        approval_request_id: str,
        *,
        confirmed: bool,
        transport_context: TransportContext,
    ) -> object: ...


class QueryRuntimeApplication:
    def __init__(
        self,
        query_service: QueryService,
        *,
        scheduler_status: Callable[[], object] | None = None,
        system_monitor_status: Callable[[dict[str, object]], dict[str, object]]
        | None = None,
        system_updates_check: Callable[[dict[str, object]], dict[str, object]]
        | None = None,
        intent_pipeline: IntentPipeline | None = None,
        task_context: Callable[[], object] | None = None,
        plan_store: PlanStore | None = None,
        plan_executor: PlanExecutor | None = None,
        task_ledger: TaskHistory | None = None,
        workspace: WorkspaceHistory | None = None,
        workspace_controller: WorkspaceController | None = None,
        model_catalog: Callable[[], dict[str, object]] | None = None,
        model_decision: Callable[[dict[str, object]], dict[str, object]] | None = None,
        ollama_provider_status: Callable[[], dict[str, object]] | None = None,
        ollama_provider_decision: Callable[[dict[str, object]], dict[str, object]]
        | None = None,
        software_snapshot: Callable[[], dict[str, object]] | None = None,
        software_prepare: Callable[[dict[str, object]], dict[str, object]]
        | None = None,
        software_respond: Callable[[dict[str, object]], dict[str, object]]
        | None = None,
        software_control: Callable[[dict[str, object]], dict[str, object]]
        | None = None,
        software_restore: Callable[[dict[str, object]], dict[str, object]]
        | None = None,
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
        self.workspace_controller = workspace_controller
        self.model_catalog_callback = model_catalog
        self.model_decision_callback = model_decision
        self.ollama_provider_status_callback = ollama_provider_status
        self.ollama_provider_decision_callback = ollama_provider_decision
        self.software_snapshot_callback = software_snapshot
        self.software_prepare_callback = software_prepare
        self.software_respond_callback = software_respond
        self.software_control_callback = software_control
        self.software_restore_callback = software_restore
        if intent_pipeline is not None and task_context is None:
            raise ValueError("task_context is required with intent_pipeline")
        if (plan_store is None) != (plan_executor is None):
            raise ValueError("plan_store and plan_executor must be configured together")

    def capabilities(self) -> list[str]:
        capabilities = [
            "documents.query.search",
            "documents.pdf.extract",
            "storage.collection.snapshot",
            "storage.volumes.read",
            "storage.volumes.enroll",
        ]
        if self.intent_pipeline is not None:
            capabilities.append("intent.compile")
        if self.plan_executor is not None:
            capabilities.append("execution.plan.execute")
            if (
                "storage.materialize.plan-copy"
                in self.plan_executor.available_capabilities()
            ):
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
        if self.workspace_controller is not None:
            capabilities.extend(("workspace.submit", "workspace.approval.respond"))
        if self.model_catalog_callback is not None:
            capabilities.append("models.catalog.read")
        if self.model_decision_callback is not None:
            capabilities.append("models.lifecycle.respond")
        if self.ollama_provider_status_callback is not None:
            capabilities.append("providers.ollama.read")
        if self.ollama_provider_decision_callback is not None:
            capabilities.append("providers.ollama.respond")
        if (
            self.model_catalog_callback is not None
            or self.ollama_provider_status_callback is not None
        ):
            capabilities.append("inference.lifecycle.read")
        if self.software_snapshot_callback is not None:
            capabilities.extend(
                (
                    "software.catalog.read",
                    "software.tasks.read",
                    "software.backups.read",
                )
            )
        if self.software_prepare_callback is not None:
            capabilities.extend(("software.install.prepare", "software.remove.prepare"))
        if self.software_respond_callback is not None:
            capabilities.extend(("software.install.commit", "software.remove.commit"))
        if self.software_control_callback is not None:
            capabilities.append("software.tasks.control")
        if self.software_restore_callback is not None:
            capabilities.append("software.backups.restore")
        return capabilities

    def software_snapshot(self, payload: dict[str, object]) -> dict[str, object]:
        if self.software_snapshot_callback is None:
            raise RuntimeError("software_manager_unavailable")
        if payload:
            raise ValueError("software snapshot does not accept fields")
        result = self.software_snapshot_callback()
        if not isinstance(result, dict):
            raise RuntimeError("software_manager_invalid_response")
        return result

    def software_prepare(
        self, payload: dict[str, object], *, transport_context: TransportContext
    ) -> dict[str, object]:
        if self.software_prepare_callback is None:
            raise RuntimeError("software_manager_unavailable")
        self._require_secure_transport(transport_context)
        action = payload.get("action")
        if action == "install":
            expected = {
                "action",
                "application_id",
                "locale",
                "install_location",
                "selected_options",
            }
            capability = "software.install.prepare"
            selected_options = payload.get("selected_options")
            if not isinstance(selected_options, list) or any(
                not isinstance(item, str) for item in selected_options
            ):
                raise ValueError("selected_options must be a string list")
            arguments = {
                "application_id": payload.get("application_id"),
                "locale": payload.get("locale"),
                "install_location": payload.get("install_location"),
                "selected_options": tuple(selected_options),
            }
        elif action == "remove":
            expected = {"action", "application_id", "create_backup"}
            capability = "software.remove.prepare"
            arguments = {
                "application_id": payload.get("application_id"),
                "create_backup": payload.get("create_backup"),
            }
        else:
            raise ValueError("unknown software action")
        if set(payload) != expected:
            raise ValueError("software prepare fields are invalid")
        self._authorize_software(
            capability,
            ExecutionPhase.PREPARE,
            arguments,
            transport_context,
            approval_granted=False,
        )
        result = self.software_prepare_callback(payload)
        if not isinstance(result, dict):
            raise RuntimeError("software_manager_invalid_response")
        return result

    def software_respond(
        self, payload: dict[str, object], *, transport_context: TransportContext
    ) -> dict[str, object]:
        if self.software_respond_callback is None:
            raise RuntimeError("software_manager_unavailable")
        self._require_secure_transport(transport_context)
        if set(payload) != {"task_id", "confirmed", "action", "final_confirmation"}:
            raise ValueError("software confirmation fields are required")
        if not isinstance(payload.get("confirmed"), bool):
            raise ValueError("confirmed must be boolean")
        action = payload.get("action")
        final_confirmation = payload.get("final_confirmation")
        if action not in {"install", "remove"} or not isinstance(
            final_confirmation, bool
        ):
            raise ValueError("software confirmation is invalid")
        if payload["confirmed"]:
            capability = f"software.{action}.commit"
            arguments = {"task_id": payload.get("task_id")}
            if action == "remove":
                arguments["final_confirmation"] = final_confirmation
            self._authorize_software(
                capability,
                ExecutionPhase.COMMIT,
                arguments,
                transport_context,
                approval_granted=True,
            )
        result = self.software_respond_callback(payload)
        if not isinstance(result, dict):
            raise RuntimeError("software_manager_invalid_response")
        return result

    def software_control(
        self, payload: dict[str, object], *, transport_context: TransportContext
    ) -> dict[str, object]:
        if self.software_control_callback is None:
            raise RuntimeError("software_manager_unavailable")
        self._require_secure_transport(transport_context)
        if set(payload) != {"task_id", "action"}:
            raise ValueError("task_id and action are required")
        if payload.get("action") not in {"pause", "resume", "cancel"}:
            raise ValueError("unknown software control action")
        self._authorize_software(
            "software.tasks.control",
            ExecutionPhase.EXECUTE,
            {"task_id": payload.get("task_id"), "action": payload.get("action")},
            transport_context,
            approval_granted=False,
        )
        result = self.software_control_callback(payload)
        if not isinstance(result, dict):
            raise RuntimeError("software_manager_invalid_response")
        return result

    def software_restore(
        self, payload: dict[str, object], *, transport_context: TransportContext
    ) -> dict[str, object]:
        if self.software_restore_callback is None:
            raise RuntimeError("software_manager_unavailable")
        self._require_secure_transport(transport_context)
        if set(payload) != {"backup_id", "confirmed"}:
            raise ValueError("backup_id and confirmation are required")
        backup_id = payload.get("backup_id")
        if not isinstance(backup_id, str) or not backup_id or len(backup_id) > 256:
            raise ValueError("backup_id is invalid")
        if payload.get("confirmed") is not True:
            raise ValueError("restore confirmation is required")
        self._authorize_software(
            "software.backup.restore",
            ExecutionPhase.COMMIT,
            {"backup_id": backup_id},
            transport_context,
            approval_granted=True,
        )
        result = self.software_restore_callback({"backup_id": backup_id})
        if not isinstance(result, dict):
            raise RuntimeError("software_manager_invalid_response")
        return result

    @staticmethod
    def _require_secure_transport(transport_context: TransportContext) -> None:
        if transport_context.transport.value not in {"internal", "unix_peer"}:
            raise PermissionError("secure_transport_required")

    @staticmethod
    def _authorize_software(
        capability: str,
        phase: ExecutionPhase,
        arguments: dict[str, object],
        transport_context: TransportContext,
        *,
        approval_granted: bool,
    ) -> None:
        gateway = PermissionGateway(
            builtin_policies(), capability_source=lambda: (capability,)
        )
        policy = gateway.policy(capability)
        invocation = CapabilityInvocation(
            request_id=str(uuid4()),
            plan_id=str(uuid4()),
            step_id="step_software",
            capability_id=capability,
            phase=phase,
            arguments=arguments,
            declared_risk=policy.risk.value,
            declared_approval_required=policy.plan_approval_required,
            context=ExecutionContext(
                transport_context,
                frozenset({"software.manage"}),
                approval_granted,
            ),
        )
        decision = gateway.evaluate(invocation)
        if not decision.allowed:
            raise PermissionError(decision.reason_code)

    def storage_volumes(self, payload: dict[str, object]) -> dict[str, object]:
        if set(payload) - {"refresh", "available_only"}:
            raise ValueError("unknown storage volume field")
        refresh = payload.get("refresh", False)
        available_only = payload.get("available_only", False)
        if not isinstance(refresh, bool) or not isinstance(available_only, bool):
            raise ValueError("storage volume flags must be booleans")
        volumes = (
            self.query_service.enrollment.discover()
            if refresh
            else self.query_service.enrollment.list(available_only=available_only)
        )
        if refresh and available_only:
            volumes = tuple(volume for volume in volumes if volume.is_available)
        return {"schema_version": 1, "volumes": [asdict(volume) for volume in volumes]}

    def storage_permission(self, payload: dict[str, object]) -> dict[str, object]:
        if set(payload) != {"volume_id", "permission"}:
            raise ValueError("volume_id and permission are required")
        volume_id = self._string(payload, "volume_id")
        try:
            permission = PermissionLevel(self._string(payload, "permission"))
        except ValueError as error:
            raise ValueError("unknown storage permission") from error
        return asdict(
            self.query_service.enrollment.set_permission(volume_id, permission)
        )

    def inference_lifecycle(self, payload: dict[str, object]) -> dict[str, object]:
        if payload:
            raise ValueError("inference lifecycle does not accept fields")
        errors: list[dict[str, str]] = []
        try:
            provider = self.ollama_provider_status({})
        except Exception:
            provider = {
                "schema_version": 1,
                "provider_id": "ollama",
                "display_name": "Ollama",
                "state": "error",
                "installed": False,
                "managed": False,
                "version": None,
                "decision": "unset",
                "prompt_required": False,
                "progress_percent": None,
                "completed_bytes": 0,
                "total_bytes": None,
                "reason": "provider_status_unavailable",
            }
            errors.append(
                {"component": "provider.ollama", "code": "status_unavailable"}
            )
        try:
            catalog = self.model_catalog({})
        except Exception:
            catalog = {
                "schema_version": 1,
                "models": [
                    self._unavailable_model(
                        "workspace.qwen",
                        "qwen3.5:2b",
                        "Qwen 3.5 2B",
                        "workspace_base",
                        True,
                    ),
                    self._unavailable_model(
                        "assistant.llama",
                        "llama3.2:3b",
                        "Llama 3.2 3B",
                        "optional_assistant",
                        False,
                    ),
                    self._unavailable_model(
                        "semantic.selector",
                        "qwen3-embedding:0.6b",
                        "Qwen 3 Embedding 0.6B",
                        "semantic_selector",
                        False,
                    ),
                ],
            }
            errors.append({"component": "model.ollama", "code": "catalog_unavailable"})
        models = catalog.get("models")
        if not isinstance(models, list):
            models = []
            errors.append({"component": "model.ollama", "code": "catalog_invalid"})
        valid_models = [value for value in models if isinstance(value, dict)]
        known_ids = {value.get("model_id") for value in valid_models}
        required_models = (
            (
                "workspace.qwen",
                "qwen3.5:2b",
                "Qwen 3.5 2B",
                "workspace_base",
                True,
            ),
            (
                "assistant.llama",
                "llama3.2:3b",
                "Llama 3.2 3B",
                "optional_assistant",
                False,
            ),
            (
                "semantic.selector",
                "qwen3-embedding:0.6b",
                "Qwen 3 Embedding 0.6B",
                "semantic_selector",
                False,
            ),
        )
        for model_id, provider_model, display_name, role, required in required_models:
            if model_id in known_ids:
                continue
            valid_models.append(
                self._unavailable_model(
                    model_id,
                    provider_model,
                    display_name,
                    role,
                    required,
                    reason="model_status_missing",
                )
            )
            errors.append({"component": model_id, "code": "status_missing"})
        # An executable on disk is not an inference provider until its loopback
        # API is reachable. Treating `installed` as ready can bind the model
        # catalog to a different Ollama daemon and model store.
        provider_ready = provider.get("state") == "ready"
        projected: list[dict[str, object]] = []
        for value in valid_models:
            model = dict(value)
            lifecycle_state = model.get("state", "error")
            if provider_ready:
                model["effective_state"] = lifecycle_state
                model["blocked_by"] = None
                model["effective_prompt_required"] = (
                    model.get("prompt_required") is True
                )
            else:
                model["effective_state"] = "blocked"
                model["blocked_by"] = "provider.ollama"
                model["effective_prompt_required"] = False
            projected.append(model)
        overall = self._inference_overall_state(provider, projected, errors)
        return {
            "schema_version": 1,
            "state": overall,
            "provider": provider,
            "models": projected,
            "errors": errors,
        }

    def ollama_provider_status(self, payload: dict[str, object]) -> dict[str, object]:
        if self.ollama_provider_status_callback is None:
            raise RuntimeError("ollama_provider_unavailable")
        if payload:
            raise ValueError("provider status does not accept fields")
        result = self.ollama_provider_status_callback()
        if not isinstance(result, dict):
            raise RuntimeError("ollama_provider_invalid_response")
        return result

    def respond_to_ollama_provider(
        self, payload: dict[str, object]
    ) -> dict[str, object]:
        if self.ollama_provider_decision_callback is None:
            raise RuntimeError("ollama_provider_unavailable")
        if set(payload) != {"decision"}:
            raise ValueError("exactly decision is required")
        decision = self._string(payload, "decision")
        if decision not in {"install", "later", "never"}:
            raise ValueError("unknown provider decision")
        result = self.ollama_provider_decision_callback({"decision": decision})
        if not isinstance(result, dict):
            raise RuntimeError("ollama_provider_invalid_response")
        return result

    @staticmethod
    def _unavailable_model(
        model_id: str,
        provider_model: str,
        display_name: str,
        role: str,
        required: bool,
        *,
        reason: str = "model_catalog_unavailable",
    ) -> dict[str, object]:
        return {
            "model_id": model_id,
            "provider": "ollama",
            "provider_model": provider_model,
            "display_name": display_name,
            "role": role,
            "required": required,
            "installed": False,
            "decision": "unset",
            "prompt_required": False,
            "state": "error",
            "progress_percent": None,
            "completed_bytes": 0,
            "total_bytes": None,
            "reason": reason,
        }

    @staticmethod
    def _inference_overall_state(
        provider: dict[str, object],
        models: list[dict[str, object]],
        errors: list[dict[str, str]],
    ) -> str:
        if errors or provider.get("state") in {"error", "unsupported"}:
            return "error"
        provider_state = provider.get("state")
        if provider_state in {"starting", "downloading", "installing"}:
            return "provider_preparing"
        if provider_state != "ready":
            return (
                "action_required"
                if provider.get("prompt_required") is True
                else "blocked"
            )
        states = {model.get("state") for model in models}
        if states & {"error", "unavailable"}:
            return "error"
        if states & {"starting", "downloading"}:
            return "models_preparing"
        if any(model.get("prompt_required") is True for model in models):
            return "action_required"
        required = [model for model in models if model.get("required") is True]
        return (
            "ready"
            if required and all(model.get("state") == "ready" for model in required)
            else "blocked"
        )

    def model_catalog(self, payload: dict[str, object]) -> dict[str, object]:
        if self.model_catalog_callback is None:
            raise RuntimeError("model_catalog_unavailable")
        if payload:
            raise ValueError("model catalog does not accept fields")
        result = self.model_catalog_callback()
        if not isinstance(result, dict):
            raise RuntimeError("model_catalog_invalid_response")
        return result

    def respond_to_model(self, payload: dict[str, object]) -> dict[str, object]:
        if self.model_decision_callback is None:
            raise RuntimeError("model_lifecycle_unavailable")
        if set(payload) != {"model_id", "decision"}:
            raise ValueError("model_id and decision are required")
        model_id = self._string(payload, "model_id")
        decision = self._string(payload, "decision")
        if re.fullmatch(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+){1,7}", model_id) is None:
            raise ValueError("unknown model_id")
        catalog = self.model_catalog({})
        models = catalog.get("models")
        if not isinstance(models, list) or model_id not in {
            item.get("model_id") for item in models if isinstance(item, dict)
        }:
            raise ValueError("unknown model_id")
        if decision not in {"download", "later", "never"}:
            raise ValueError("unknown model decision")
        result = self.model_decision_callback(
            {"model_id": model_id, "decision": decision}
        )
        if not isinstance(result, dict):
            raise RuntimeError("model_lifecycle_invalid_response")
        return result

    def workspace_submit(
        self,
        payload: dict[str, object],
        *,
        transport_context: TransportContext,
    ) -> object:
        if self.workspace_controller is None:
            raise RuntimeError("workspace_controller_unavailable")
        if "text" not in payload or set(payload) - {"text", "attachments"}:
            raise ValueError("text and optional attachments are required")
        text = self._string(payload, "text")
        if not text.strip() or len(text) > 4_000:
            raise ValueError("workspace text must contain from 1 to 4000 characters")
        if "attachments" not in payload:
            return self.workspace_controller.submit(
                text, transport_context=transport_context
            )
        from ai_native_workspace import WorkspaceAttachment

        entries = payload["attachments"]
        if not isinstance(entries, list) or len(entries) > 8:
            raise ValueError("attachments must be a bounded list")
        if any(
            not isinstance(item, dict) or set(item) != {"kind", "name"}
            for item in entries
        ):
            raise ValueError("attachment metadata must contain kind and name only")
        return self.workspace_controller.submit(
            text,
            transport_context=transport_context,
            attachments=tuple(WorkspaceAttachment(**item) for item in entries),
        )

    def workspace_approval(
        self,
        payload: dict[str, object],
        *,
        transport_context: TransportContext,
    ) -> object:
        if self.workspace_controller is None:
            raise RuntimeError("workspace_controller_unavailable")
        if set(payload) != {"approval_request_id", "confirmed"}:
            raise ValueError("approval_request_id and confirmed are required")
        confirmed = payload.get("confirmed")
        if not isinstance(confirmed, bool):
            raise ValueError("confirmed must be a boolean")
        return self.workspace_controller.respond_to_approval(
            self._string(payload, "approval_request_id"),
            confirmed=confirmed,
            transport_context=transport_context,
        )

    def workspace_messages(self, payload: dict[str, object]) -> tuple[object, ...]:
        if self.workspace is None:
            raise RuntimeError("workspace_unavailable")
        if set(payload) - {"limit"}:
            raise ValueError("unknown workspace message field")
        limit = payload.get("limit", 200)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 500
        ):
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
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
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
        return self.query_service.search(self._document_query(payload))

    def search_page(self, payload: dict[str, object]) -> SearchPage:
        return self.query_service.search_page(self._document_query(payload))

    def _document_query(self, payload: dict[str, object]) -> DocumentQuery:
        unknown = set(payload) - {
            "text",
            "content_match",
            "mode",
            "name_contains",
            "extensions",
            "volume_ids",
            "limit",
            "offset",
        }
        if unknown:
            raise ValueError(f"unknown search fields: {sorted(unknown)}")
        text = self._string(payload, "text")
        raw_content_match = payload.get("content_match")
        content_match = None
        if raw_content_match is not None:
            if not isinstance(raw_content_match, str):
                raise ValueError("content_match must be a string")
            try:
                content_match = ContentMatch(raw_content_match)
            except ValueError as error:
                raise ValueError("unsupported content match") from error
        if bool(text) and content_match is None:
            raise ValueError("content_match is required for content search")
        if not text and content_match is not None:
            raise ValueError("content_match requires non-empty text")
        raw_mode = payload.get("mode", "content" if text else "metadata")
        if not isinstance(raw_mode, str):
            raise ValueError("mode must be a string")
        try:
            mode = SearchMode(raw_mode)
        except ValueError as error:
            raise ValueError("unsupported search mode") from error
        name_contains = self._strings(payload, "name_contains")
        extensions = self._strings(payload, "extensions")
        volume_ids = self._strings(payload, "volume_ids")
        limit = payload.get("limit", 20)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        offset = payload.get("offset", 0)
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise ValueError("offset must be an integer")
        return DocumentQuery(
            mode=mode,
            text=text,
            content_match=content_match,
            name_contains=name_contains,
            extensions=extensions,
            volume_ids=volume_ids,
            limit=limit,
            offset=offset,
        )

    def index_status(self) -> dict[str, object]:
        status: dict[str, object] = {
            "catalog": self.query_service.catalog.status(),
            "content_index": self.query_service.indexer.get_index_status(),
        }
        if self.scheduler_status is not None:
            scheduler = self.scheduler_status()
            status["scheduler"] = (
                asdict(scheduler) if is_dataclass(scheduler) else scheduler
            )
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
        result = self.intent_pipeline.compile_and_plan(
            text, context=self.task_context()
        )
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
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, str) for item in value
        ):
            raise ValueError(f"{key} must be a list of strings")
        if len(value) > 100:
            raise ValueError(f"{key} has too many values")
        return tuple(value)
