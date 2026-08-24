import shutil
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from ai_native_intents import CompilationState, ModelTurn, ModelTurnKind, TaskContext
from ai_native_orchestrator import (
    ApprovalRequest,
    OrchestrationResult,
    OrchestrationState,
    SearchOutput,
    StepExecution,
    StepState,
)
from ai_native_permissions import TransportContext
from ai_native_query import SearchCoverage
from ai_native_turns import TurnRouter
from ai_native_workspace import (
    MessageKind,
    WorkspaceBusyError,
    WorkspaceRuntime,
    WorkspaceStage,
    WorkspaceStore,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Model:
    def __init__(self, turn):
        self.turn = turn
        self.route_calls = 0
        self.summary_calls = 0
        self.requests = []

    def route(self, request):
        self.route_calls += 1
        self.requests.append(request)
        return self.turn

    def summarize_result(self, facts, *, locale):
        self.summary_calls += 1
        return f"Готово. Найдено: {facts['found_items']}."


class ComposingModel(Model):
    def __init__(self, turn, reply):
        super().__init__(turn)
        self.reply = reply
        self.compose_calls = 0

    def compose_conversation(self, request, *, system_result):
        self.compose_calls += 1
        self.requests.append(request)
        return self.reply


class SplitModel(Model):
    def __init__(self, turn, classification, chat_reply="Привет!"):
        super().__init__(turn)
        self.classification = classification
        self.chat_reply = chat_reply
        self.classify_calls = 0
        self.chat_calls = 0

    def classify_turn(self, request):
        self.classify_calls += 1
        return self.classification

    def respond_chat(self, request):
        self.chat_calls += 1
        self.requests.append(request)
        return self.chat_reply


class FailingChatModel(SplitModel):
    def respond_chat(self, request):
        self.chat_calls += 1
        raise RuntimeError("transient local model response failure")


class BrokenClassifierModel(SplitModel):
    def classify_turn(self, request):
        self.classify_calls += 1
        raise ValueError("malformed classifier envelope")


class Compiler:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def compile_payload(self, _payload, *, text, context):
        self.calls += 1
        return self.result


class Executor:
    def __init__(self, result, approval_result=None):
        self.result = result
        self.approval_result = approval_result
        self.called = threading.Event()

    def execute(self, _plan, *, transport_context=None):
        self.called.set()
        return self.result

    def respond_to_approval(self, _request_id, *, confirmed, transport_context=None):
        self.called.set()
        return self.approval_result


class BlockingExecutor(Executor):
    def __init__(self, result):
        super().__init__(result)
        self.release = threading.Event()

    def execute(self, plan, *, transport_context=None):
        self.called.set()
        self.release.wait(2)
        return self.result


def completed_result(*, count=3, task_id=None):
    task_id = task_id or str(uuid4())
    output = SearchOutput(str(uuid4()), count, ())
    return OrchestrationResult(
        task_id,
        str(uuid4()),
        OrchestrationState.COMPLETED,
        (StepExecution("step_search", "documents.query.search", StepState.COMPLETED, output),),
    )


class WorkspaceRuntimeTests(unittest.TestCase):
    def test_malformed_classifier_uses_chat_and_cannot_reach_hallucinated_tool(self):
        model = BrokenClassifierModel(
            ModelTurn(ModelTurnKind.ACTION, intent_payload={"hallucinated": True}),
            {},
            "Привет! Чем могу помочь?",
        )
        compiler = Compiler(None)
        executor = Executor(None)
        runtime = WorkspaceRuntime(
            self.store,
            model,
            compiler,
            executor,
            lambda: TaskContext(locale="ru"),
            turn_router=TurnRouter(model),
        )
        try:
            run = runtime.submit(
                "Привет", transport_context=TransportContext.internal()
            )
            self.wait_for(run.run_id, WorkspaceStage.COMPLETED)
        finally:
            runtime.close()

        self.assertEqual(model.chat_calls, 1)
        self.assertEqual(model.route_calls, 0)
        self.assertEqual(compiler.calls, 0)
        self.assertFalse(executor.called.is_set())

    def test_classifier_clarification_falls_back_to_chat_without_tool_route(self):
        model = SplitModel(
            ModelTurn(ModelTurnKind.ACTION, intent_payload={"hallucinated": True}),
            {
                "kind": "clarification",
                "language": "ru",
                "confidence": 0.4,
                "conversation_text": None,
                "action_text": None,
            },
            "Хлеб обычно выпекают при 175–190 °C.",
        )
        runtime = WorkspaceRuntime(
            self.store,
            model,
            Compiler(None),
            Executor(None),
            lambda: TaskContext(locale="ru"),
            turn_router=TurnRouter(model),
        )
        try:
            run = runtime.submit(
                "А при какой температуре печь хлеб",
                transport_context=TransportContext.internal(),
            )
            self.wait_for(run.run_id, WorkspaceStage.COMPLETED)
        finally:
            runtime.close()

        self.assertEqual(model.classify_calls, 1)
        self.assertEqual(model.route_calls, 0)
        self.assertEqual(model.chat_calls, 1)
        self.assertEqual(self.store.list_messages()[-1].content, "Хлеб обычно выпекают при 175–190 °C.")

    def test_failed_chat_retries_once_and_never_falls_into_tool_route(self):
        text = "Так а что ты можешь в целом и какой ты ИИ"
        model = FailingChatModel(
            ModelTurn(ModelTurnKind.CONVERSATION, response_text="Я локальный помощник системы."),
            {
                "kind": "conversation",
                "language": "ru",
                "confidence": 0.95,
                "conversation_text": text,
                "action_text": None,
            },
        )
        runtime = WorkspaceRuntime(
            self.store,
            model,
            Compiler(None),
            Executor(None),
            lambda: TaskContext(locale="ru"),
            turn_router=TurnRouter(model),
        )
        try:
            run = runtime.submit(text, transport_context=TransportContext.internal())
            self.wait_for(run.run_id, WorkspaceStage.FAILED)
        finally:
            runtime.close()

        self.assertEqual(model.chat_calls, 2)
        self.assertEqual(model.route_calls, 0)
        self.assertEqual(self.store.list_messages()[-1].content, "Не удалось обработать запрос.")

    def test_split_conversation_never_calls_intent_route_or_compiler(self):
        model = SplitModel(
            ModelTurn(ModelTurnKind.CONVERSATION, response_text="unused"),
            {
                "kind": "conversation",
                "language": "ru",
                "confidence": 0.99,
                "conversation_text": "Привет, как дела?",
                "action_text": None,
            },
            "Всё хорошо. Чем помочь?",
        )
        compiler = Compiler(None)
        runtime = WorkspaceRuntime(
            self.store,
            model,
            compiler,
            Executor(None),
            lambda: TaskContext(locale="ru"),
            turn_router=TurnRouter(model),
        )
        try:
            run = runtime.submit(
                "Привет, как дела?", transport_context=TransportContext.internal()
            )
            self.wait_for(run.run_id, WorkspaceStage.COMPLETED)
        finally:
            runtime.close()
        self.assertEqual(model.classify_calls, 1)
        self.assertEqual(model.chat_calls, 1)
        self.assertEqual(model.route_calls, 0)
        self.assertEqual(compiler.calls, 0)

    def test_split_mixed_turn_chats_then_compiles_only_exact_action_fragment(self):
        model = SplitModel(
            ModelTurn(ModelTurnKind.ACTION, intent_payload={"safe": True}),
            {
                "kind": "mixed",
                "language": "ru",
                "confidence": 0.95,
                "conversation_text": "Расскажи про хлеб",
                "action_text": "найди PDF",
            },
            "Хлеб обычно выпекают при 180 градусах.",
        )
        compiler = Compiler(SimpleNamespace(state=CompilationState.READY, plan=object()))
        runtime = WorkspaceRuntime(
            self.store,
            model,
            compiler,
            Executor(completed_result(count=2)),
            lambda: TaskContext(locale="ru"),
            turn_router=TurnRouter(model),
        )
        try:
            run = runtime.submit(
                "Расскажи про хлеб и найди PDF",
                transport_context=TransportContext.internal(),
            )
            self.wait_for(run.run_id, WorkspaceStage.COMPLETED)
        finally:
            runtime.close()
        self.assertEqual(model.chat_calls, 1)
        self.assertEqual(model.route_calls, 1)
        self.assertEqual(model.requests[-1].user_text, "найди PDF")
        self.assertEqual(compiler.calls, 1)

    def setUp(self):
        self.root = PROJECT_ROOT / "tmp" / "workspace-runtime-tests" / str(uuid4())
        self.store = WorkspaceStore(self.root / "workspace.sqlite3")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def wait_for(self, run_id, *stages):
        for _ in range(200):
            run = self.store.run(run_id)
            if run.stage in stages:
                return run
            threading.Event().wait(0.01)
        self.fail(f"workspace run did not reach {stages}")

    def test_mixed_turn_combines_qwen_reply_with_trusted_incomplete_zero(self):
        model = ComposingModel(
            ModelTurn(ModelTurnKind.ACTION, intent_payload={"safe": True}),
            "Для хлеба сначала уточните его вид.",
        )
        output = SearchOutput(
            str(uuid4()),
            0,
            (),
            ("index_coverage_incomplete",),
            "metadata",
            "pdf",
            SearchCoverage(False, "updating", ("system",), 42),
        )
        result = OrchestrationResult(
            str(uuid4()),
            str(uuid4()),
            OrchestrationState.COMPLETED,
            (StepExecution("step_search", "documents.query.search", StepState.COMPLETED, output),),
        )
        runtime = WorkspaceRuntime(
            self.store,
            model,
            Compiler(SimpleNamespace(state=CompilationState.READY, plan=object())),
            Executor(result),
            lambda: TaskContext(locale="ru"),
        )
        try:
            run = runtime.submit(
                "Расскажи про хлеб и найди все PDF",
                transport_context=TransportContext.internal(),
            )
            self.wait_for(run.run_id, WorkspaceStage.COMPLETED)
        finally:
            runtime.close()

        message = next(
            item for item in self.store.list_messages()
            if item.kind is MessageKind.TASK_RESULT
        )
        self.assertIn("Найдено файлов: 0", message.content)
        self.assertIn("результат неполный", message.content)
        self.assertEqual(message.source.value, "tool")
        conversation = [
            item for item in self.store.list_messages()
            if item.kind is MessageKind.CONVERSATION and item.role.value == "assistant"
        ]
        self.assertIn("Для хлеба", conversation[-1].content)
        self.assertEqual(conversation[-1].source.value, "qwen")
        self.assertEqual(model.compose_calls, 1)

    def test_conversation_uses_one_model_call_and_creates_no_task(self):
        model = Model(ModelTurn(ModelTurnKind.CONVERSATION, response_text="Привет!"))
        compiler = Compiler(None)
        executor = Executor(None)
        runtime = WorkspaceRuntime(
            self.store, model, compiler, executor, lambda: TaskContext(locale="ru")
        )
        try:
            run = runtime.submit("Привет", transport_context=TransportContext.internal())
            run = self.wait_for(run.run_id, WorkspaceStage.COMPLETED)
        finally:
            runtime.close()

        self.assertEqual(model.route_calls, 1)
        self.assertEqual(model.summary_calls, 0)
        self.assertEqual(compiler.calls, 0)
        self.assertIsNone(run.task_id)
        self.assertEqual([item.content for item in self.store.list_messages()], ["Привет", "Привет!"])

    def test_conversation_history_is_read_from_existing_workspace_memory(self):
        first_model = Model(
            ModelTurn(ModelTurnKind.CONVERSATION, response_text="Привет!")
        )
        first = WorkspaceRuntime(
            self.store,
            first_model,
            Compiler(None),
            Executor(None),
            lambda: TaskContext(locale="ru"),
        )
        try:
            run = first.submit("Привет", transport_context=TransportContext.internal())
            self.wait_for(run.run_id, WorkspaceStage.COMPLETED)
        finally:
            first.close()

        second_model = Model(
            ModelTurn(ModelTurnKind.CONVERSATION, response_text="Продолжаем.")
        )
        second = WorkspaceRuntime(
            self.store,
            second_model,
            Compiler(None),
            Executor(None),
            lambda: TaskContext(locale="ru"),
        )
        try:
            run = second.submit("А дальше?", transport_context=TransportContext.internal())
            self.wait_for(run.run_id, WorkspaceStage.COMPLETED)
        finally:
            second.close()

        self.assertEqual(
            [(item.role, item.content) for item in second_model.requests[0].history],
            [("user", "Привет"), ("assistant", "Привет!")],
        )

    def test_unsupported_action_is_never_sent_to_executor(self):
        model = Model(
            ModelTurn(
                ModelTurnKind.UNSUPPORTED_ACTION,
                response_text="Я понял запрос на создание папки.",
                unsupported_actions=("создать папку",),
            )
        )
        executor = Executor(None)
        runtime = WorkspaceRuntime(
            self.store,
            model,
            Compiler(None),
            executor,
            lambda: TaskContext(locale="ru"),
        )
        try:
            run = runtime.submit(
                "Создай папку", transport_context=TransportContext.internal()
            )
            run = self.wait_for(run.run_id, WorkspaceStage.COMPLETED)
        finally:
            runtime.close()

        self.assertFalse(executor.called.is_set())
        self.assertIsNone(run.task_id)
        self.assertIn("Ничего не было выполнено", self.store.list_messages()[-1].content)

    def test_mixed_supported_and_unsupported_proposal_executes_nothing(self):
        model = Model(
            ModelTurn(
                ModelTurnKind.ACTION,
                response_text="Я понял обе части запроса.",
                intent_payload={"safe": True},
                unsupported_actions=("создать папку",),
            )
        )
        compiler = Compiler(
            SimpleNamespace(state=CompilationState.READY, plan=object())
        )
        executor = Executor(completed_result())
        runtime = WorkspaceRuntime(
            self.store,
            model,
            compiler,
            executor,
            lambda: TaskContext(locale="ru"),
        )
        try:
            run = runtime.submit(
                "Найди PDF и создай папку",
                transport_context=TransportContext.internal(),
            )
            self.wait_for(run.run_id, WorkspaceStage.COMPLETED)
        finally:
            runtime.close()

        self.assertEqual(compiler.calls, 0)
        self.assertFalse(executor.called.is_set())

    def test_action_routes_once_and_summarizes_confirmed_result_once(self):
        model = Model(ModelTurn(ModelTurnKind.ACTION, intent_payload={"safe": True}))
        plan = object()
        compiler = Compiler(SimpleNamespace(state=CompilationState.READY, plan=plan))
        result = completed_result(count=4)
        executor = Executor(result)
        runtime = WorkspaceRuntime(
            self.store, model, compiler, executor, lambda: TaskContext(locale="ru")
        )
        try:
            run = runtime.submit(
                "Найди документы", transport_context=TransportContext.internal()
            )
            run = self.wait_for(run.run_id, WorkspaceStage.COMPLETED)
        finally:
            runtime.close()

        self.assertEqual(model.route_calls, 1)
        self.assertEqual(model.summary_calls, 1)
        self.assertEqual(compiler.calls, 1)
        self.assertEqual(run.task_id, result.run_id)
        self.assertEqual(self.store.list_messages()[-1].kind, MessageKind.TASK_RESULT)

    def test_action_may_include_natural_qwen_reply_before_system_execution(self):
        model = Model(
            ModelTurn(
                ModelTurnKind.ACTION,
                response_text="Понял, проверю документы.",
                intent_payload={"safe": True},
            )
        )
        compiler = Compiler(
            SimpleNamespace(state=CompilationState.READY, plan=object())
        )
        result = completed_result(count=1)
        runtime = WorkspaceRuntime(
            self.store,
            model,
            compiler,
            Executor(result),
            lambda: TaskContext(locale="ru"),
        )
        try:
            run = runtime.submit(
                "Найди документы", transport_context=TransportContext.internal()
            )
            self.wait_for(run.run_id, WorkspaceStage.COMPLETED)
        finally:
            runtime.close()

        messages = self.store.list_messages()
        self.assertIn("Понял, проверю документы.", [item.content for item in messages])
        self.assertEqual(messages[-1].kind, MessageKind.TASK_RESULT)

    def test_clarification_never_executes_a_plan(self):
        model = Model(ModelTurn(ModelTurnKind.ACTION, intent_payload={"safe": True}))
        compiler = Compiler(
            SimpleNamespace(
                state=CompilationState.NEEDS_CLARIFICATION,
                plan=None,
                clarification_question="Какие документы найти?",
            )
        )
        executor = Executor(None)
        runtime = WorkspaceRuntime(
            self.store, model, compiler, executor, lambda: TaskContext(locale="ru")
        )
        try:
            run = runtime.submit("Найди их", transport_context=TransportContext.internal())
            self.wait_for(run.run_id, WorkspaceStage.COMPLETED)
        finally:
            runtime.close()

        self.assertFalse(executor.called.is_set())
        self.assertEqual(self.store.list_messages()[-1].kind, MessageKind.CLARIFICATION)

    def test_approval_id_survives_polling_and_completion_links_task(self):
        approval_id = str(uuid4())
        task_id = str(uuid4())
        approval = ApprovalRequest(
            approval_id,
            str(uuid4()),
            "step_copy",
            "copy",
            "desktop",
            2,
            10,
            ("one.pdf", "two.pdf"),
            300,
        )
        pending = OrchestrationResult(
            task_id,
            approval.plan_id,
            OrchestrationState.AWAITING_APPROVAL,
            (),
            approval_request=approval,
        )
        completed = completed_result(count=0, task_id=task_id)
        model = Model(ModelTurn(ModelTurnKind.ACTION, intent_payload={"safe": True}))
        compiler = Compiler(SimpleNamespace(state=CompilationState.READY, plan=object()))
        executor = Executor(pending, completed)
        runtime = WorkspaceRuntime(
            self.store, model, compiler, executor, lambda: TaskContext(locale="ru")
        )
        try:
            run = runtime.submit("Скопируй их", transport_context=TransportContext.internal())
            run = self.wait_for(run.run_id, WorkspaceStage.AWAITING_APPROVAL)
            self.assertEqual(run.approval_request_id, approval_id)
            self.assertEqual(self.store.run_for_approval(approval_id).run_id, run.run_id)
            runtime.respond_to_approval(
                approval_id,
                confirmed=True,
                transport_context=TransportContext.internal(),
            )
            run = self.store.run(run.run_id)
        finally:
            runtime.close()

        self.assertEqual(run.stage, WorkspaceStage.COMPLETED)
        self.assertEqual(run.task_id, task_id)

    def test_bounded_queue_rejects_excess_work_without_storing_ghost_message(self):
        model = Model(ModelTurn(ModelTurnKind.ACTION, intent_payload={"safe": True}))
        compiler = Compiler(SimpleNamespace(state=CompilationState.READY, plan=object()))
        executor = BlockingExecutor(completed_result())
        runtime = WorkspaceRuntime(
            self.store,
            model,
            compiler,
            executor,
            lambda: TaskContext(locale="ru"),
            workers=1,
            max_pending=1,
        )
        try:
            first = runtime.submit("Первая", transport_context=TransportContext.internal())
            self.assertTrue(executor.called.wait(1))
            with self.assertRaises(WorkspaceBusyError):
                runtime.submit("Вторая", transport_context=TransportContext.internal())
            executor.release.set()
            self.wait_for(first.run_id, WorkspaceStage.COMPLETED)
        finally:
            executor.release.set()
            runtime.close()

        self.assertNotIn("Вторая", [item.content for item in self.store.list_messages()])

    def test_model_download_state_is_visible_without_invoking_qwen(self):
        model = Model(ModelTurn(ModelTurnKind.CONVERSATION, response_text="unused"))
        runtime = WorkspaceRuntime(
            self.store,
            model,
            Compiler(None),
            Executor(None),
            lambda: TaskContext(locale="ru"),
            model_status=lambda: {"state": "downloading", "progress_percent": 42},
        )
        try:
            run = runtime.submit("Привет", transport_context=TransportContext.internal())
            run = self.wait_for(run.run_id, WorkspaceStage.FAILED)
        finally:
            runtime.close()

        self.assertEqual(model.route_calls, 0)
        self.assertIn("42%", self.store.list_messages()[-1].content)
        self.assertIsNone(run.task_id)

    def test_model_consent_state_is_visible_without_invoking_qwen(self):
        model = Model(ModelTurn(ModelTurnKind.CONVERSATION, response_text="unused"))
        runtime = WorkspaceRuntime(
            self.store,
            model,
            Compiler(None),
            Executor(None),
            lambda: TaskContext(locale="ru"),
            model_status=lambda: {
                "state": "consent_required",
                "reason": "user_decision_required",
            },
        )
        try:
            run = runtime.submit("Привет", transport_context=TransportContext.internal())
            self.wait_for(run.run_id, WorkspaceStage.FAILED)
        finally:
            runtime.close()

        self.assertEqual(model.route_calls, 0)
        self.assertIn("Выберите", self.store.list_messages()[-1].content)


if __name__ == "__main__":
    unittest.main()
