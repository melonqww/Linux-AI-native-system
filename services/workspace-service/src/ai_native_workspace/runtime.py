"""Asynchronous model → plan → execution coordinator for the system workspace."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import BoundedSemaphore
from typing import Protocol

from ai_native_intents import (
    CompilationState,
    IntentCompiler,
    ModelRequest,
    ModelTurn,
    ModelTurnKind,
    TaskContext,
)
from ai_native_intents.schema import INTENT_OUTPUT_SCHEMA, MODEL_INSTRUCTIONS
from ai_native_orchestrator import (
    CopyOutput,
    OrchestrationResult,
    OrchestrationState,
    SearchOutput,
)
from ai_native_permissions import TransportContext

from .contracts import MessageKind, MessageRole, WorkspaceRun, WorkspaceStage
from .store import WorkspaceStore


class RouteProvider(Protocol):
    def route(self, request: ModelRequest) -> ModelTurn: ...

    def summarize_result(self, facts: Mapping[str, object], *, locale: str) -> str: ...


class PlanExecutor(Protocol):
    def execute(
        self, plan: object, *, transport_context: TransportContext | None = None
    ) -> OrchestrationResult: ...

    def respond_to_approval(
        self,
        approval_request_id: str,
        *,
        confirmed: bool,
        transport_context: TransportContext | None = None,
    ) -> OrchestrationResult: ...


class WorkspaceBusyError(RuntimeError):
    pass


class WorkspaceRuntime:
    """Runs bounded workspace jobs without blocking the panel IPC thread."""

    def __init__(
        self,
        store: WorkspaceStore,
        model: RouteProvider,
        compiler: IntentCompiler,
        executor: PlanExecutor,
        context: Callable[[], TaskContext],
        *,
        workers: int = 2,
        max_pending: int = 32,
    ) -> None:
        if not 1 <= workers <= 8:
            raise ValueError("workers must be between 1 and 8")
        if not workers <= max_pending <= 128:
            raise ValueError("max_pending must be between workers and 128")
        self.store = store
        self.model = model
        self.compiler = compiler
        self.executor = executor
        self.context = context
        self._pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="workspace-runtime"
        )
        self._slots = BoundedSemaphore(max_pending)

    def submit(
        self, text: str, *, transport_context: TransportContext
    ) -> WorkspaceRun:
        normalized = self._user_text(text)
        if not self._slots.acquire(blocking=False):
            raise WorkspaceBusyError("workspace_queue_full")
        try:
            message = self.store.append_message(
                MessageRole.USER, MessageKind.CONVERSATION, normalized
            )
            run = self.store.create_run(message.message_id)
            future = self._pool.submit(
                self._process, run.run_id, normalized, transport_context
            )
            future.add_done_callback(lambda _future: self._slots.release())
            return run
        except Exception:
            self._slots.release()
            raise

    def respond_to_approval(
        self,
        approval_request_id: str,
        *,
        confirmed: bool,
        transport_context: TransportContext,
    ) -> OrchestrationResult:
        workspace_run = self.store.run_for_approval(approval_request_id)
        self.store.transition(workspace_run.run_id, WorkspaceStage.EXECUTING)
        locale = self.context().locale
        try:
            result = self.executor.respond_to_approval(
                approval_request_id,
                confirmed=confirmed,
                transport_context=transport_context,
            )
            self._finish_result(workspace_run.run_id, result, locale)
            return result
        except Exception:
            self._finish_failure(workspace_run.run_id, locale)
            raise

    def close(self, *, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=False)

    def _process(
        self, run_id: str, text: str, transport_context: TransportContext
    ) -> None:
        locale = self.context().locale
        try:
            self.store.transition(run_id, WorkspaceStage.UNDERSTANDING)
            turn = self.model.route(
                ModelRequest(
                    user_text=text,
                    locale=locale,
                    context=self.context().for_model(),
                    output_schema=deepcopy(INTENT_OUTPUT_SCHEMA),
                    instructions=MODEL_INSTRUCTIONS,
                )
            )
            if turn.kind is ModelTurnKind.CONVERSATION:
                if turn.response_text is None:
                    raise ValueError("conversation response is missing")
                self.store.transition(run_id, WorkspaceStage.SUMMARIZING)
                response = self.store.append_message(
                    MessageRole.ASSISTANT,
                    MessageKind.CONVERSATION,
                    turn.response_text,
                )
                self.store.complete(run_id, response.message_id)
                return
            if turn.kind is not ModelTurnKind.ACTION or turn.intent_payload is None:
                raise ValueError("model route is invalid")

            self.store.transition(run_id, WorkspaceStage.PLANNING)
            compilation = self.compiler.compile_payload(
                turn.intent_payload,
                text=text,
                context=self.context(),
            )
            if compilation.state is not CompilationState.READY or compilation.plan is None:
                question = compilation.clarification_question or self._clarification(locale)
                self.store.transition(run_id, WorkspaceStage.SUMMARIZING)
                response = self.store.append_message(
                    MessageRole.ASSISTANT, MessageKind.CLARIFICATION, question
                )
                self.store.complete(run_id, response.message_id)
                return

            self.store.append_message(
                MessageRole.SYSTEM,
                MessageKind.NOTICE,
                "План готов. Начинаю выполнение."
                if locale.startswith("ru")
                else "The plan is ready. Starting execution.",
            )
            self.store.transition(run_id, WorkspaceStage.EXECUTING)
            result = self.executor.execute(
                compilation.plan, transport_context=transport_context
            )
            if result.state is OrchestrationState.AWAITING_APPROVAL:
                approval = result.approval_request
                if approval is None:
                    raise RuntimeError("approval result has no request")
                self.store.awaiting_approval(
                    run_id,
                    task_id=result.run_id,
                    approval_request_id=approval.approval_request_id,
                )
                self.store.append_message(
                    MessageRole.ASSISTANT,
                    MessageKind.NOTICE,
                    "Для продолжения требуется подтверждение."
                    if locale.startswith("ru")
                    else "Confirmation is required to continue.",
                    task_id=result.run_id,
                )
                return
            self._finish_result(run_id, result, locale)
        except Exception:
            self._finish_failure(run_id, locale)

    def _finish_result(
        self, workspace_run_id: str, result: OrchestrationResult, locale: str
    ) -> None:
        if result.state is OrchestrationState.COMPLETED:
            self.store.transition(workspace_run_id, WorkspaceStage.SUMMARIZING)
            facts = self._result_facts(result)
            try:
                content = self.model.summarize_result(facts, locale=locale)
            except Exception:
                content = self._fallback_summary(facts, locale)
            message = self.store.append_message(
                MessageRole.ASSISTANT,
                MessageKind.TASK_RESULT,
                content,
                task_id=result.run_id,
            )
            self.store.finish(
                workspace_run_id,
                WorkspaceStage.COMPLETED,
                message.message_id,
                task_id=result.run_id,
            )
            return

        stage = (
            WorkspaceStage.CANCELLED
            if result.state is OrchestrationState.CANCELLED
            else WorkspaceStage.FAILED
        )
        content = (
            "Задача отменена."
            if stage is WorkspaceStage.CANCELLED and locale.startswith("ru")
            else "The task was cancelled."
            if stage is WorkspaceStage.CANCELLED
            else "Не удалось выполнить задачу."
            if locale.startswith("ru")
            else "The task could not be completed."
        )
        message = self.store.append_message(
            MessageRole.ASSISTANT,
            MessageKind.TASK_RESULT,
            content,
            task_id=result.run_id,
        )
        self.store.finish(
            workspace_run_id, stage, message.message_id, task_id=result.run_id
        )

    def _finish_failure(self, run_id: str, locale: str) -> None:
        try:
            current = self.store.run(run_id)
            if current.stage in {
                WorkspaceStage.COMPLETED,
                WorkspaceStage.FAILED,
                WorkspaceStage.CANCELLED,
            }:
                return
            message = self.store.append_message(
                MessageRole.ASSISTANT,
                MessageKind.NOTICE,
                "Не удалось обработать запрос."
                if locale.startswith("ru")
                else "The request could not be processed.",
                task_id=current.task_id,
            )
            self.store.finish(
                run_id,
                WorkspaceStage.FAILED,
                message.message_id,
                task_id=current.task_id,
            )
        except Exception:
            return

    @staticmethod
    def _result_facts(result: OrchestrationResult) -> dict[str, object]:
        found = 0
        copied = 0
        completed_steps = 0
        for step in result.steps:
            if step.state.value == "completed":
                completed_steps += 1
            if isinstance(step.output, SearchOutput):
                found += step.output.result_count
            elif isinstance(step.output, CopyOutput):
                copied += step.output.copied_count
        return {
            "state": result.state.value,
            "completed_steps": completed_steps,
            "found_items": found,
            "copied_items": copied,
        }

    @staticmethod
    def _fallback_summary(facts: Mapping[str, object], locale: str) -> str:
        found = int(facts.get("found_items", 0))
        copied = int(facts.get("copied_items", 0))
        if locale.startswith("ru"):
            parts = ["Готово."]
            if found:
                parts.append(f"Найдено объектов: {found}.")
            if copied:
                parts.append(f"Скопировано объектов: {copied}.")
            return " ".join(parts)
        parts = ["Done."]
        if found:
            parts.append(f"Items found: {found}.")
        if copied:
            parts.append(f"Items copied: {copied}.")
        return " ".join(parts)

    @staticmethod
    def _clarification(locale: str) -> str:
        return (
            "Уточните, пожалуйста, что именно нужно сделать."
            if locale.startswith("ru")
            else "Please clarify what should be done."
        )

    @staticmethod
    def _user_text(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("text must be a string")
        text = value.strip()
        if not text or len(text) > 4_000:
            raise ValueError("text must contain from 1 to 4000 characters")
        if any(ord(character) < 32 and character not in "\n\t" for character in text):
            raise ValueError("text contains control characters")
        return text
