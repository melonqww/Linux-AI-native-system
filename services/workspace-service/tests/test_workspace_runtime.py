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

    def route(self, _request):
        self.route_calls += 1
        return self.turn

    def summarize_result(self, facts, *, locale):
        self.summary_calls += 1
        return f"Готово. Найдено: {facts['found_items']}."


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


if __name__ == "__main__":
    unittest.main()
