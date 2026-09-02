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
    ModelHistoryMessage,
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
from ai_native_turns import (
    TurnClassification,
    TurnHistoryMessage,
    TurnKind,
    TurnRequest,
    TurnRouter,
)

from .contracts import MessageKind, MessageRole, WorkspaceRun, WorkspaceStage
from .store import WorkspaceStore


class RouteProvider(Protocol):
    def route(self, request: ModelRequest) -> ModelTurn: ...

    def summarize_result(self, facts: Mapping[str, object], *, locale: str) -> str: ...

    def compose_conversation(
        self, request: ModelRequest, *, system_result: str
    ) -> str | None: ...


class CapabilityRouter(Protocol):
    def candidates(self, text: str) -> tuple[object, ...]: ...

    def available_operations(self) -> tuple[str, ...]: ...


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
        model_status: Callable[[], Mapping[str, object]] | None = None,
        turn_router: TurnRouter | None = None,
        capability_router: CapabilityRouter | None = None,
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
        self.model_status = model_status
        self.turn_router = turn_router
        self.capability_router = capability_router
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
                self._process,
                run.run_id,
                message.message_id,
                normalized,
                transport_context,
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
            self._finish_result(
                workspace_run.run_id,
                result,
                locale,
                compose_conversation=(
                    self.turn_router is None and self.capability_router is None
                ),
            )
            return result
        except Exception:
            self._finish_failure(workspace_run.run_id, locale)
            raise

    def close(self, *, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=False)

    def _process(
        self,
        run_id: str,
        user_message_id: str,
        text: str,
        transport_context: TransportContext,
    ) -> None:
        locale = self.context().locale
        try:
            self.store.transition(run_id, WorkspaceStage.UNDERSTANDING)
            readiness = self._model_readiness()
            if readiness is not None:
                self._finish_model_unavailable(run_id, readiness, locale)
                return
            model_text = text
            allowed_operations: tuple[str, ...] | None = None
            candidates: tuple[object, ...] = ()
            if self.capability_router is not None:
                candidates = self.capability_router.candidates(text)
                if not candidates and self.turn_router is None:
                    chat_response = self._respond_chat(text, locale, user_message_id)
                    self.store.transition(run_id, WorkspaceStage.SUMMARIZING)
                    response = self.store.append_message(
                        MessageRole.ASSISTANT,
                        MessageKind.CONVERSATION,
                        chat_response,
                    )
                    self.store.complete(run_id, response.message_id)
                    return
                allowed_operations = tuple(
                    dict.fromkeys(
                        str(getattr(candidate, "operation")) for candidate in candidates
                    )
                ) or None
            if self.turn_router is not None:
                classification = self._classify_turn(
                    text, locale, user_message_id
                )
                if classification is None or classification.kind is TurnKind.CLARIFICATION:
                    # A malformed or low-confidence classifier cannot be
                    # upgraded to an action by lexical candidates. Chat may
                    # clarify the complete original message, but tools remain
                    # physically unavailable on this turn.
                    chat_response = self._respond_chat(
                        text, locale, user_message_id
                    )
                    self.store.transition(run_id, WorkspaceStage.SUMMARIZING)
                    response = self.store.append_message(
                        MessageRole.ASSISTANT,
                        MessageKind.CONVERSATION,
                        chat_response,
                    )
                    self.store.complete(run_id, response.message_id)
                    return
                if classification is not None and classification.kind in {
                    TurnKind.ACTION,
                    TurnKind.MIXED,
                }:
                    if classification.action_text is None:
                        raise ValueError("action classification has no text")
                    action_candidates: tuple[object, ...] = ()
                    if self.capability_router is not None:
                        action_candidates = self.capability_router.candidates(
                            classification.action_text
                        )
                    if action_candidates:
                        allowed_operations = tuple(
                            dict.fromkeys(
                                str(getattr(candidate, "operation"))
                                for candidate in action_candidates
                            )
                        )
                    elif candidates:
                        # The model produced a syntactically valid split, but
                        # its alleged action no longer grounds any capability
                        # found in the original message. Route the immutable
                        # original through the restricted semantic tools and
                        # do not answer the wrongly extracted chat fragment.
                        classification = None
                        model_text = text
                    else:
                        allowed_operations = self._available_operations()

                if classification is not None and classification.kind in {
                    TurnKind.CONVERSATION,
                    TurnKind.MIXED,
                }:
                    conversation_text = classification.conversation_text
                    if conversation_text is None:
                        raise ValueError("conversation classification has no text")
                    try:
                        chat_response = self._respond_chat(
                            conversation_text, locale, user_message_id
                        )
                    except Exception:
                        if classification.kind is TurnKind.CONVERSATION:
                            raise
                        chat_response = None
                    if chat_response is not None:
                        message = self.store.append_message(
                            MessageRole.ASSISTANT,
                            MessageKind.CONVERSATION,
                            chat_response,
                        )
                        if classification.kind is TurnKind.CONVERSATION:
                            self.store.transition(run_id, WorkspaceStage.SUMMARIZING)
                            self.store.complete(run_id, message.message_id)
                            return
                if classification is not None and classification.kind in {
                    TurnKind.ACTION,
                    TurnKind.MIXED,
                }:
                    if classification.action_text is None:
                        raise ValueError("action classification has no text")
                    model_text = classification.action_text
            turn = self.model.route(
                ModelRequest(
                    user_text=model_text,
                    locale=locale,
                    context=self.context().for_model(),
                    output_schema=deepcopy(INTENT_OUTPUT_SCHEMA),
                    instructions=MODEL_INSTRUCTIONS,
                    history=self._model_history(user_message_id),
                    allowed_operations=allowed_operations,
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
            if turn.kind is ModelTurnKind.UNSUPPORTED_ACTION or turn.unsupported_actions:
                if turn.response_text:
                    self.store.append_message(
                        MessageRole.ASSISTANT,
                        MessageKind.CONVERSATION,
                        turn.response_text,
                    )
                self.store.transition(run_id, WorkspaceStage.SUMMARIZING)
                response = self.store.append_message(
                    MessageRole.ASSISTANT,
                    MessageKind.CLARIFICATION,
                    self._unsupported_action(locale),
                )
                self.store.complete(run_id, response.message_id)
                return
            if turn.kind is not ModelTurnKind.ACTION or turn.intent_payload is None:
                raise ValueError("model route is invalid")

            self.store.transition(run_id, WorkspaceStage.PLANNING)
            compilation = self.compiler.compile_payload(
                turn.intent_payload,
                text=model_text,
                context=self.context(),
            )
            if turn.response_text:
                self.store.append_message(
                    MessageRole.ASSISTANT,
                    MessageKind.CONVERSATION,
                    turn.response_text,
                )
            if compilation.state is CompilationState.UNAVAILABLE:
                self.store.transition(run_id, WorkspaceStage.SUMMARIZING)
                response = self.store.append_message(
                    MessageRole.ASSISTANT,
                    MessageKind.CLARIFICATION,
                    self._unsupported_action(locale),
                )
                self.store.complete(run_id, response.message_id)
                return
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
            self._finish_result(
                run_id,
                result,
                locale,
                compose_conversation=(
                    self.turn_router is None and self.capability_router is None
                ),
            )
        except Exception:
            self._finish_failure(run_id, locale)

    def _available_operations(self) -> tuple[str, ...] | None:
        if self.capability_router is None:
            return None
        source = getattr(self.capability_router, "available_operations", None)
        if not callable(source):
            return None
        values = source()
        if not isinstance(values, tuple):
            raise ValueError("candidate router operations must be a tuple")
        operations = tuple(
            dict.fromkeys(value for value in values if isinstance(value, str) and value)
        )
        return operations or None

    def _classify_turn(
        self, text: str, locale: str, current_user_message_id: str
    ) -> TurnClassification | None:
        if self.turn_router is None:
            return None
        histories = (self._turn_history(current_user_message_id), ())
        for ordinal, history in enumerate(histories):
            if ordinal and not histories[0]:
                break
            try:
                return self.turn_router.route(
                    TurnRequest(text, locale, history=history)
                )
            except Exception:
                continue
        return None

    def _respond_chat(
        self, text: str, locale: str, current_user_message_id: str
    ) -> str:
        chat = getattr(self.model, "respond_chat", None)
        if not callable(chat):
            raise ValueError("chat provider is unavailable")
        histories = (self._conversation_history(current_user_message_id), ())
        last_error: Exception | None = None
        for history in histories:
            try:
                return chat(
                    ModelRequest(
                        user_text=text,
                        locale=locale,
                        context=self.context().for_model(),
                        output_schema={},
                        instructions="",
                        history=history,
                    )
                )
            except Exception as error:
                last_error = error
        assert last_error is not None
        raise last_error

    def _finish_result(
        self,
        workspace_run_id: str,
        result: OrchestrationResult,
        locale: str,
        *,
        compose_conversation: bool = True,
    ) -> None:
        if result.state is OrchestrationState.COMPLETED:
            self.store.transition(workspace_run_id, WorkspaceStage.SUMMARIZING)
            facts = self._result_facts(result)
            system_result = self._fallback_summary(facts, locale)
            content = system_result
            if compose_conversation:
                try:
                    workspace_run = self.store.run(workspace_run_id)
                    if workspace_run.user_message_id is None:
                        raise ValueError("workspace run has no user message")
                    user_message = self.store.message(workspace_run.user_message_id)
                    compose = getattr(self.model, "compose_conversation", None)
                    if callable(compose):
                        natural = compose(
                            ModelRequest(
                                user_text=user_message.content,
                                locale=locale,
                                context=self.context().for_model(),
                                output_schema=deepcopy(INTENT_OUTPUT_SCHEMA),
                                instructions=MODEL_INSTRUCTIONS,
                                history=self._model_history(user_message.message_id),
                            ),
                            system_result=system_result,
                        )
                        if natural:
                            self.store.append_message(
                                MessageRole.ASSISTANT,
                                MessageKind.CONVERSATION,
                                natural,
                            )
                    else:
                        content = self.model.summarize_result(facts, locale=locale)
                except Exception:
                    content = system_result
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

    def _model_readiness(self) -> Mapping[str, object] | None:
        if self.model_status is None:
            return None
        try:
            status = self.model_status()
        except Exception:
            return {"state": "unavailable", "reason": "model_manager_unavailable"}
        if not isinstance(status, Mapping):
            return {"state": "unavailable", "reason": "model_manager_invalid"}
        return None if status.get("state") == "ready" else status

    def _model_history(self, current_user_message_id: str) -> tuple[ModelHistoryMessage, ...]:
        eligible = []
        for message in self.store.list_messages(limit=50):
            if message.message_id == current_user_message_id:
                continue
            if message.role is MessageRole.USER:
                eligible.append(message)
                continue
            if message.role is MessageRole.ASSISTANT and message.kind in {
                MessageKind.CONVERSATION,
                MessageKind.CLARIFICATION,
                MessageKind.TASK_RESULT,
            }:
                eligible.append(message)

        selected: list[ModelHistoryMessage] = []
        characters = 0
        for message in reversed(eligible):
            if len(selected) >= 12:
                break
            content = message.content.strip()
            if not content or characters + len(content) > 8_000:
                continue
            role = "user" if message.role is MessageRole.USER else "assistant"
            selected.append(ModelHistoryMessage(role, content))
            characters += len(content)
        selected.reverse()
        return tuple(selected)

    def _conversation_history(
        self, current_user_message_id: str
    ) -> tuple[ModelHistoryMessage, ...]:
        eligible = [
            message
            for message in self.store.list_messages(limit=50)
            if message.message_id != current_user_message_id
            and message.role in {MessageRole.USER, MessageRole.ASSISTANT}
            and (
                message.kind is MessageKind.CONVERSATION
                or (
                    message.role is MessageRole.ASSISTANT
                    and message.kind is MessageKind.TASK_RESULT
                )
            )
        ]
        selected: list[ModelHistoryMessage] = []
        seen_assistant: set[str] = set()
        characters = 0
        for message in reversed(eligible):
            if len(selected) >= 10:
                break
            content = message.content.strip()
            fingerprint = content.casefold()
            if message.role is MessageRole.ASSISTANT:
                if fingerprint in seen_assistant:
                    continue
                seen_assistant.add(fingerprint)
            if not content or characters + len(content) > 6_000:
                continue
            selected.append(ModelHistoryMessage(message.role.value, content))
            characters += len(content)
        selected.reverse()
        return tuple(selected)

    def _turn_history(
        self, current_user_message_id: str
    ) -> tuple[TurnHistoryMessage, ...]:
        return tuple(
            TurnHistoryMessage(item.role, item.content)
            for item in self._conversation_history(current_user_message_id)
        )

    def _finish_model_unavailable(
        self,
        run_id: str,
        status: Mapping[str, object],
        locale: str,
    ) -> None:
        state = status.get("state")
        progress = status.get("progress_percent")
        reason = status.get("reason")
        russian = locale.startswith("ru")
        if state == "downloading":
            suffix = f": {progress}%" if isinstance(progress, int) else ""
            content = (
                f"Модель загружается{suffix}. Повторите запрос после завершения."
                if russian
                else f"The model is downloading{suffix}. Try again when it is ready."
            )
        elif state in {"starting", "checking"}:
            content = (
                "Локальная модель подготавливается. Повторите запрос через некоторое время."
                if russian
                else "The local model is starting. Try again shortly."
            )
        elif state == "consent_required":
            content = (
                "Для работы рабочей области нужна базовая модель. Выберите, загружать ли её."
                if russian
                else "Workspace needs its base model. Choose whether to download it."
            )
        elif state == "deferred":
            content = (
                "Загрузка базовой модели отложена."
                if russian
                else "The base model download was deferred."
            )
        elif state == "declined":
            content = (
                "Базовая модель отключена в настройках."
                if russian
                else "The base model is disabled in settings."
            )
        elif reason in {"ollama_not_installed", "ollama_consent_required"}:
            content = (
                "Ollama не установлен, поэтому локальная модель недоступна."
                if russian
                else "Ollama is not installed, so the local model is unavailable."
            )
        elif reason in {
            "external_server_unavailable",
            "ollama_server_unavailable",
            "ollama_provider_unavailable",
            "provider_status_unavailable",
        }:
            content = (
                "Служба Ollama сейчас недоступна. Запустите её и повторите запрос."
                if russian
                else "The Ollama service is unavailable. Start it and try again."
            )
        else:
            content = (
                "Локальная модель сейчас недоступна."
                if russian
                else "The local model is currently unavailable."
            )
        message = self.store.append_message(
            MessageRole.ASSISTANT, MessageKind.NOTICE, content
        )
        self.store.finish(
            run_id, WorkspaceStage.FAILED, message.message_id
        )

    @staticmethod
    def _result_facts(result: OrchestrationResult) -> dict[str, object]:
        found = 0
        total_matches = 0
        total_is_exact = True
        copied = 0
        completed_steps = 0
        search_mode = ""
        criteria = ""
        coverage_complete = True
        coverage_state = "ready"
        inaccessible = 0
        sample_paths: list[str] = []
        for step in result.steps:
            if step.state.value == "completed":
                completed_steps += 1
            if isinstance(step.output, SearchOutput):
                found += step.output.result_count
                total_matches += (
                    step.output.result_count
                    if step.output.total_matches is None
                    else step.output.total_matches
                )
                total_is_exact = total_is_exact and step.output.total_is_exact
                search_mode = step.output.mode
                criteria = step.output.criteria
                sample_paths.extend(item.path for item in step.output.results[:5])
                if step.output.coverage is not None:
                    coverage_complete = coverage_complete and step.output.coverage.complete
                    coverage_state = step.output.coverage.state
                    inaccessible += step.output.coverage.inaccessible_items
            elif isinstance(step.output, CopyOutput):
                copied += step.output.copied_count
        return {
            "state": result.state.value,
            "completed_steps": completed_steps,
            "found_items": found,
            "total_matches": total_matches,
            "total_is_exact": total_is_exact,
            "copied_items": copied,
            "search_mode": search_mode,
            "criteria": criteria,
            "coverage_complete": coverage_complete,
            "coverage_state": coverage_state,
            "inaccessible_items": inaccessible,
            "sample_paths": tuple(sample_paths[:5]),
        }

    @staticmethod
    def _fallback_summary(facts: Mapping[str, object], locale: str) -> str:
        found = int(facts.get("found_items", 0))
        total = int(facts.get("total_matches", found))
        copied = int(facts.get("copied_items", 0))
        if locale.startswith("ru"):
            parts = []
            if facts.get("search_mode"):
                if total > found:
                    qualifier = "не менее " if not facts.get("total_is_exact", True) else ""
                    parts.append(
                        f"Поиск завершён. Показано файлов: {found}; "
                        f"всего совпадений: {qualifier}{total}."
                    )
                else:
                    parts.append(f"Поиск завершён. Найдено файлов: {found}.")
                if not bool(facts.get("coverage_complete", False)):
                    parts.append(
                        "Индекс разрешённых дисков ещё формируется, поэтому результат неполный."
                    )
                inaccessible = int(facts.get("inaccessible_items", 0))
                if inaccessible:
                    parts.append(f"Недоступных объектов: {inaccessible}.")
                paths = facts.get("sample_paths", ())
                if isinstance(paths, tuple) and paths:
                    parts.append("Примеры: " + "; ".join(paths) + ".")
            if copied:
                parts.append(f"Скопировано объектов: {copied}.")
            return " ".join(parts) or "Задача выполнена."
        parts = []
        if facts.get("search_mode"):
            if total > found:
                qualifier = "at least " if not facts.get("total_is_exact", True) else ""
                parts.append(
                    f"Search completed. Files shown: {found}; total matches: {qualifier}{total}."
                )
            else:
                parts.append(f"Search completed. Files found: {found}.")
            if not bool(facts.get("coverage_complete", False)):
                parts.append("The allowed-drive index is still being built, so this result is incomplete.")
        if copied:
            parts.append(f"Items copied: {copied}.")
        return " ".join(parts) or "Task completed."

    @staticmethod
    def _clarification(locale: str) -> str:
        return (
            "Уточните, пожалуйста, что именно нужно сделать."
            if locale.startswith("ru")
            else "Please clarify what should be done."
        )

    @staticmethod
    def _unsupported_action(locale: str) -> str:
        return (
            "Я понял, что требуется действие системы, но подходящий модуль пока не подключён. Ничего не было выполнено."
            if locale.startswith("ru")
            else "I understood that a system action is needed, but no suitable module is connected yet. Nothing was executed."
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
