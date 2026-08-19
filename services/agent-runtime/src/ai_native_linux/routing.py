"""Transport-neutral routing and redacted runtime error envelopes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol
from uuid import uuid4

from ai_native_permissions import TransportContext
from ai_native_ledger import InvalidTransitionError, TaskNotFoundError


class RuntimeApplication(Protocol):
    def capabilities(self) -> list[str]: ...
    def search(self, payload: dict[str, object]) -> list[object]: ...
    def index_status(self) -> dict[str, object]: ...
    def system_status(self) -> dict[str, object]: ...
    def compile_intent(self, payload: dict[str, object]) -> object: ...
    def execute_plan(
        self, payload: dict[str, object], *, transport_context: TransportContext
    ) -> object: ...
    def respond_to_approval(
        self, payload: dict[str, object], *, transport_context: TransportContext
    ) -> object: ...
    def tasks(self) -> tuple[object, ...]: ...
    def task_detail(self, payload: dict[str, object]) -> object: ...
    def cancel_task(self, payload: dict[str, object]) -> object: ...
    def continue_task(self, payload: dict[str, object]) -> object: ...


@dataclass(frozen=True)
class RuntimeResponse:
    status: int
    payload: object


class RuntimeRouter:
    def __init__(
        self,
        application: RuntimeApplication,
        *,
        transport_context: TransportContext | None = None,
        allow_r1: bool = True,
    ) -> None:
        self.application = application
        self.transport_context = transport_context or TransportContext.internal()
        self.allow_r1 = allow_r1

    def dispatch(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        request_id: str | None = None,
        transport_context: TransportContext | None = None,
    ) -> RuntimeResponse:
        correlation_id = request_id or str(uuid4())
        try:
            if method == "GET":
                return self._get(path, correlation_id)
            if method == "POST":
                if payload is None:
                    raise ValueError("request_body_required")
                return self._post(
                    path,
                    payload,
                    correlation_id,
                    transport_context or self.transport_context,
                )
            return self.error(405, "method_not_allowed", False, correlation_id)
        except TaskNotFoundError:
            return self.error(404, "task_not_found", False, correlation_id)
        except InvalidTransitionError:
            return self.error(409, "task_action_not_available", False, correlation_id)
        except RuntimeError:
            return self.error(503, "service_unavailable", True, correlation_id)
        except (ValueError, KeyError):
            return self.error(400, "invalid_request", False, correlation_id)
        except Exception:
            return self.error(500, "internal_error", True, correlation_id)

    def _get(self, path: str, request_id: str) -> RuntimeResponse:
        if path == "/v1/health":
            return RuntimeResponse(200, {"status": "ok"})
        if path == "/v1/capabilities":
            capabilities = self.application.capabilities()
            if not self.allow_r1:
                restricted = {
                    "execution.r1.copy",
                    "tasks.activity.detail",
                    "tasks.activity.control",
                }
                capabilities = [item for item in capabilities if item not in restricted]
            return RuntimeResponse(200, {"capabilities": capabilities})
        if path == "/v1/index-status":
            return RuntimeResponse(200, self.application.index_status())
        if path == "/v1/system-status":
            return RuntimeResponse(200, self.application.system_status())
        if path == "/v1/tasks":
            return RuntimeResponse(
                200, {"tasks": [asdict(item) for item in self.application.tasks()]}
            )
        return self.error(404, "not_found", False, request_id)

    def _post(
        self,
        path: str,
        payload: dict[str, object],
        request_id: str,
        transport_context: TransportContext,
    ) -> RuntimeResponse:
        if path == "/v1/search":
            return RuntimeResponse(
                200, {"results": [asdict(item) for item in self.application.search(payload)]}
            )
        if path == "/v1/intent/compile":
            return RuntimeResponse(200, asdict(self.application.compile_intent(payload)))
        if path == "/v1/plan/execute":
            return RuntimeResponse(
                200,
                asdict(
                    self.application.execute_plan(
                        payload, transport_context=transport_context
                    )
                ),
            )
        if path == "/v1/approval/respond":
            if not self.allow_r1:
                return self.error(403, "secure_transport_required", False, request_id)
            return RuntimeResponse(
                200,
                asdict(
                    self.application.respond_to_approval(
                        payload, transport_context=transport_context
                    )
                ),
            )
        if path in {
            "/v1/tasks/detail",
            "/v1/tasks/cancel",
            "/v1/tasks/continue",
        }:
            if not self.allow_r1:
                return self.error(403, "secure_transport_required", False, request_id)
            operation = {
                "/v1/tasks/detail": self.application.task_detail,
                "/v1/tasks/cancel": self.application.cancel_task,
                "/v1/tasks/continue": self.application.continue_task,
            }[path]
            return RuntimeResponse(200, asdict(operation(payload)))
        return self.error(404, "not_found", False, request_id)

    @staticmethod
    def error(
        status: int,
        code: str,
        retryable: bool,
        request_id: str | None = None,
    ) -> RuntimeResponse:
        return RuntimeResponse(
            status,
            {
                "error": {
                    "code": code,
                    "request_id": request_id or str(uuid4()),
                    "retryable": retryable,
                }
            },
        )
