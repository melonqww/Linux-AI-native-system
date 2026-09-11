"""Asynchronous model → plan → execution coordinator for the system workspace."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import re
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
    destination_role,
)
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
from .input_policy import (
    WorkspaceAttachment,
    unavailable_input,
    input_notice,
    safe_chat_reply,
)


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
        self,
        text: str,
        *,
        transport_context: TransportContext,
        attachments: tuple[WorkspaceAttachment, ...] = (),
    ) -> WorkspaceRun:
        normalized = self._user_text(text)
        if (
            not isinstance(attachments, tuple)
            or len(attachments) > 8
            or any(not isinstance(item, WorkspaceAttachment) for item in attachments)
        ):
            raise ValueError("invalid attachments")
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
                bool(unavailable_input(normalized, attachments)),
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
        input_unavailable: bool = False,
    ) -> None:
        locale = self.context().locale
        try:
            self.store.transition(run_id, WorkspaceStage.UNDERSTANDING)
            resumed_payload = None
            pending = self.store.take_clarification(
                transport_context.principal, user_message_id
            )
            if (
                pending
                and pending.get("collection") == self.context().active_collection_id
            ):
                if re.fullmatch(
                    r"\s*(?:no|cancel|no[,.]?\s*cancel(?:\s*it)?|нет|отмена|не надо)[.!]?\s*",
                    text,
                    re.I,
                ):
                    self._complete_reply(
                        run_id,
                        "Операция не выполнена."
                        if locale.startswith("ru")
                        else "No operation was performed.",
                    )
                    return
                role = destination_role(text, clarification=True)
                if role is not None:
                    text = pending["text"] + " " + text
                    resumed_payload = self._resume_copy_intent(
                        pending.get("intent_payload"), role
                    )
                    if resumed_payload is None:
                        self._complete_reply(
                            run_id,
                            self._clarification(locale),
                            MessageKind.CLARIFICATION,
                        )
                        return
            evidence_source = getattr(
                self.capability_router, "requested_operations", None
            )
            requested = (
                tuple(evidence_source(text)) if callable(evidence_source) else None
            )
            required_source = getattr(
                self.capability_router, "required_operations", None
            )
            required = (
                tuple(required_source(text))
                if callable(required_source)
                else requested
            )
            if input_unavailable:
                self._complete_reply(
                    run_id, input_notice(locale), MessageKind.INPUT_UNAVAILABLE
                )
                return
            if resumed_payload is not None:
                self._execute_intent(
                    run_id,
                    resumed_payload,
                    text=text,
                    locale=locale,
                    transport_context=transport_context,
                    requested=requested,
                    required=required,
                )
                return
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
                allowed_operations = (
                    tuple(
                        dict.fromkeys(
                            str(getattr(candidate, "operation"))
                            for candidate in candidates
                        )
                    )
                    or None
                )
            if self.turn_router is not None:
                classification = self._classify_turn(text, locale, user_message_id)
                if requested == ():
                    reply = self._respond_chat(text, locale, user_message_id)
                    self._complete_reply(run_id, reply)
                    return
                if (
                    classification is None
                    or classification.kind is TurnKind.CLARIFICATION
                ):
                    # A malformed or low-confidence classifier cannot be
                    # upgraded to an action by lexical candidates. Chat may
                    # clarify the complete original message, but tools remain
                    # physically unavailable on this turn.
                    chat_response = self._respond_chat(text, locale, user_message_id)
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
                        allowed_operations = requested or self._available_operations()

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
                        if input_unavailable:
                            chat_response = input_notice(locale)
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
            if requested:
                allowed_operations = requested
            model_history = self._model_history(user_message_id)
            turn = self.model.route(
                self._intent_request(
                    model_text,
                    locale,
                    history=model_history,
                    allowed_operations=allowed_operations,
                )
            )
            if required:
                turn = self._complete_required_operations(
                    turn,
                    text=model_text,
                    locale=locale,
                    history=model_history,
                    required=required,
                )
            if turn.kind is ModelTurnKind.CLARIFICATION:
                if turn.clarification_key == "copy_destination":
                    self.store.save_clarification(
                        transport_context.principal,
                        user_message_id,
                        {
                            "text": text,
                            "collection": self.context().active_collection_id,
                            "intent_payload": turn.pending_intent_payload,
                        },
                    )
                self._complete_reply(
                    run_id,
                    turn.response_text or self._clarification(locale),
                    MessageKind.CLARIFICATION,
                )
                return
            if turn.kind is ModelTurnKind.CONVERSATION:
                if turn.response_text is None:
                    raise ValueError("conversation response is missing")
                self.store.transition(run_id, WorkspaceStage.SUMMARIZING)
                response = self.store.append_message(
                    MessageRole.ASSISTANT,
                    MessageKind.CONVERSATION,
                    self._clarification(locale)
                    if requested
                    else safe_chat_reply(turn.response_text, locale),
                )
                self.store.complete(run_id, response.message_id)
                return
            if (
                turn.kind is ModelTurnKind.UNSUPPORTED_ACTION
                or turn.unsupported_actions
            ):
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
            if turn.response_text and requested is None:
                self.store.append_message(
                    MessageRole.ASSISTANT,
                    MessageKind.CONVERSATION,
                    turn.response_text,
                )
            self._execute_intent(
                run_id,
                turn.intent_payload,
                text=model_text,
                locale=locale,
                transport_context=transport_context,
                requested=requested,
                required=required,
            )
        except Exception:
            self._finish_failure(run_id, locale)

    def _execute_intent(
        self,
        run_id: str,
        payload: dict[str, object],
        *,
        text: str,
        locale: str,
        transport_context: TransportContext,
        requested: tuple[str, ...] | None,
        required: tuple[str, ...] | None,
    ) -> None:
        if requested is not None:
            operations = payload.get("operations")
            if not isinstance(operations, list) or any(
                not isinstance(item, Mapping) for item in operations
            ):
                self._complete_reply(
                    run_id, self._clarification(locale), MessageKind.CLARIFICATION
                )
                return
            proposed = {item.get("kind") for item in operations}
            if (
                not proposed
                or not proposed.issubset(set(requested))
                or (required is not None and not set(required).issubset(proposed))
            ):
                self._complete_reply(
                    run_id, self._clarification(locale), MessageKind.CLARIFICATION
                )
                return
        self.store.transition(run_id, WorkspaceStage.PLANNING)
        compilation = self.compiler.compile_payload(
            payload,
            text=text,
            context=self.context(),
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

    @staticmethod
    def _resume_copy_intent(payload: object, role: str) -> dict[str, object] | None:
        if not isinstance(payload, dict):
            return None
        resumed = deepcopy(payload)
        operations = resumed.get("operations")
        if not isinstance(operations, list) or not 1 <= len(operations) <= 12:
            return None
        copies = []
        for operation in operations:
            if not isinstance(operation, dict):
                return None
            if operation.get("kind") == "copy_results":
                arguments = operation.get("arguments")
                if not isinstance(arguments, dict) or "destination" in arguments:
                    return None
                copies.append(arguments)
        if len(copies) != 1:
            return None
        copies[0]["destination"] = role
        return resumed

    def _complete_reply(
        self, run_id: str, text: str, kind: MessageKind = MessageKind.CONVERSATION
    ) -> None:
        self.store.transition(run_id, WorkspaceStage.SUMMARIZING)
        message = self.store.append_message(MessageRole.ASSISTANT, kind, text)
        self.store.complete(run_id, message.message_id)

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

    def _complete_required_operations(
        self,
        turn: ModelTurn,
        *,
        text: str,
        locale: str,
        history: tuple[ModelHistoryMessage, ...],
        required: tuple[str, ...],
    ) -> ModelTurn:
        """Repair an incomplete model graph without broadening user authority.

        ``required`` comes from the conservative, module-owned request-evidence
        boundary.  A candidate alone is never enough to enter this path.  The
        model gets one constrained opportunity per missing operation and the
        final graph is still checked by the intent compiler and permission
        gateway before anything can execute.
        """
        payload = (
            turn.intent_payload
            if turn.kind is ModelTurnKind.ACTION
            else turn.pending_intent_payload
            if turn.kind is ModelTurnKind.CLARIFICATION
            else None
        )
        merged = self._intent_payload(payload)
        proposed = self._operation_kinds(merged)
        missing = tuple(item for item in required if item not in proposed)
        if not missing:
            return turn

        clarification = turn if turn.kind is ModelTurnKind.CLARIFICATION else None
        response_text = turn.response_text
        unsupported = list(turn.unsupported_actions)
        for operation in missing:
            repair = self.model.route(
                self._intent_request(
                    text,
                    locale,
                    history=history,
                    allowed_operations=(operation,),
                )
            )
            repair_payload = (
                repair.intent_payload
                if repair.kind is ModelTurnKind.ACTION
                else repair.pending_intent_payload
                if repair.kind is ModelTurnKind.CLARIFICATION
                else None
            )
            if operation not in self._operation_kinds(repair_payload):
                return turn
            try:
                merged = self._merge_intent_payloads(merged, repair_payload, text)
            except (TypeError, ValueError):
                return turn
            unsupported.extend(repair.unsupported_actions)
            if repair.kind is ModelTurnKind.CLARIFICATION:
                clarification = repair
            elif repair.kind is not ModelTurnKind.ACTION:
                return turn

        if self._operation_kinds(merged) != set(required):
            return turn
        if clarification is not None:
            return ModelTurn(
                ModelTurnKind.CLARIFICATION,
                response_text=clarification.response_text,
                unsupported_actions=tuple(dict.fromkeys(unsupported)),
                clarification_key=clarification.clarification_key,
                pending_intent_payload=merged,
            )
        return ModelTurn(
            ModelTurnKind.ACTION,
            response_text=response_text,
            intent_payload=merged,
            unsupported_actions=tuple(dict.fromkeys(unsupported)),
        )

    @staticmethod
    def _intent_payload(value: object) -> dict[str, object] | None:
        if not isinstance(value, Mapping):
            return None
        operations = value.get("operations")
        if not isinstance(operations, list) or any(
            not isinstance(item, Mapping) for item in operations
        ):
            return None
        return deepcopy(dict(value))

    @staticmethod
    def _operation_kinds(payload: object) -> set[str]:
        if not isinstance(payload, Mapping):
            return set()
        operations = payload.get("operations")
        if not isinstance(operations, list):
            return set()
        return {
            kind
            for item in operations
            if isinstance(item, Mapping)
            and isinstance((kind := item.get("kind")), str)
        }

    @staticmethod
    def _merge_intent_payloads(
        left: dict[str, object] | None,
        right: object,
        text: str,
    ) -> dict[str, object]:
        right_payload = WorkspaceRuntime._intent_payload(right)
        if right_payload is None:
            raise ValueError("repair route has no intent payload")
        if left is None:
            return right_payload
        left_operations = left.get("operations")
        right_operations = right_payload.get("operations")
        if not isinstance(left_operations, list) or not isinstance(right_operations, list):
            raise ValueError("intent payload operations are invalid")
        operations = deepcopy(left_operations)
        previous_id = None
        if operations and isinstance(operations[-1], Mapping):
            candidate = operations[-1].get("id")
            previous_id = candidate if isinstance(candidate, str) else None
        known_ids = {
            item.get("id") for item in operations if isinstance(item, Mapping)
        }
        for raw in deepcopy(right_operations):
            if not isinstance(raw, dict):
                raise ValueError("intent repair operation is invalid")
            operation_id = raw.get("id")
            if not isinstance(operation_id, str) or operation_id in known_ids:
                raise ValueError("intent repair operation id is invalid")
            arguments = raw.get("arguments")
            dependencies = raw.get("depends_on")
            if (
                previous_id is not None
                and isinstance(arguments, dict)
                and arguments.get("results_from") == "context.active_results"
            ):
                arguments["results_from"] = previous_id
                if not isinstance(dependencies, list):
                    raise ValueError("intent repair dependencies are invalid")
                if previous_id not in dependencies:
                    dependencies.append(previous_id)
            operations.append(raw)
            known_ids.add(operation_id)
            previous_id = operation_id
        confidences = tuple(
            float(value)
            for value in (left.get("confidence"), right_payload.get("confidence"))
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        )
        return {
            "schema_version": 1,
            "language": right_payload.get("language", left.get("language")),
            "summary": text[:500],
            "confidence": min(confidences) if confidences else 0.0,
            "operations": operations,
        }

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

    def _intent_request(
        self,
        text: str,
        locale: str,
        *,
        history: tuple[ModelHistoryMessage, ...] = (),
        allowed_operations: tuple[str, ...] | None = None,
    ) -> ModelRequest:
        factory = getattr(self.compiler, "model_request", None)
        if callable(factory):
            return factory(
                text,
                context=self.context(),
                history=history,
                allowed_operations=allowed_operations,
            )
        return ModelRequest(
            user_text=text,
            locale=locale,
            context=self.context().for_model(),
            output_schema={},
            instructions="",
            history=history,
            allowed_operations=allowed_operations,
        )

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
                return safe_chat_reply(
                    chat(
                        ModelRequest(
                            user_text=text,
                            locale=locale,
                            context=self.context().for_model(),
                            output_schema={},
                            instructions="",
                            history=history,
                        )
                    ),
                    locale,
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
                            self._intent_request(
                                user_message.content,
                                locale,
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

    def _model_history(
        self, current_user_message_id: str
    ) -> tuple[ModelHistoryMessage, ...]:
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
        self.store.finish(run_id, WorkspaceStage.FAILED, message.message_id)

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
                    coverage_complete = (
                        coverage_complete and step.output.coverage.complete
                    )
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
                    qualifier = (
                        "не менее " if not facts.get("total_is_exact", True) else ""
                    )
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
                parts.append(
                    "The allowed-drive index is still being built, so this result is incomplete."
                )
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
