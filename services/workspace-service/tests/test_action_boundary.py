import gc
import shutil
import time
from datetime import datetime, UTC, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from ai_native_intents import ModelTurn, ModelTurnKind, TaskContext, CompilationState
from ai_native_permissions import TransportContext
from ai_native_turns import CapabilityCandidateRouter, CapabilityDescriptor, TurnRouter
from ai_native_workspace import (
    WorkspaceStore,
    WorkspaceRuntime,
    WorkspaceAttachment,
    MessageRole,
    MessageKind,
)
from ai_native_workspace.input_policy import safe_chat_reply
from test_workspace_runtime import SplitModel, Compiler, Executor, completed_result
from ai_native_orchestrator import (
    ApprovalRequest,
    OrchestrationResult,
    OrchestrationState,
)


@pytest.fixture
def store():
    root = Path(__file__).resolve().parents[3] / "tmp/action-boundary" / uuid4().hex
    value = WorkspaceStore(root / "workspace.sqlite3")
    yield value
    gc.collect()
    assert root.is_relative_to(Path(__file__).resolve().parents[3] / "tmp")
    shutil.rmtree(root, ignore_errors=True)


def router():
    return CapabilityCandidateRouter(
        (
            CapabilityDescriptor(
                "documents.query.search",
                "search_documents",
                "Find files",
                ("find local files", "найди файлы", "покажи файлы", "нужны только PDF"),
            ),
            CapabilityDescriptor(
                "storage.materialize.plan-copy",
                "copy_results",
                "Copy files",
                ("copy found files", "скопируй результаты"),
            ),
        )
    )


def wait(store, run):
    for _ in range(300):
        value = store.run(run.run_id)
        if value.finished_at or value.approval_request_id:
            return value
        time.sleep(0.01)
    pytest.fail("runtime did not finish")


@pytest.mark.parametrize(
    "text",
    [
        "What is 16 plus one? Answer briefly.",
        "What is 31 plus one?",
        "Привет",
        "ну и что тут? [photo attachment]",
    ],
)
def test_hallucinated_action_never_reaches_tools(store, text):
    model = SplitModel(
        ModelTurn(
            ModelTurnKind.ACTION,
            intent_payload={"operations": [{"kind": "search_documents"}]},
        ),
        {
            "kind": "action",
            "language": "en",
            "confidence": 0.99,
            "conversation_text": None,
            "action_text": text,
        },
    )
    compiler, executor = Compiler(None), Executor(None)
    runtime = WorkspaceRuntime(
        store,
        model,
        compiler,
        executor,
        lambda: TaskContext(),
        turn_router=TurnRouter(model),
        capability_router=router(),
    )
    try:
        wait(store, runtime.submit(text, transport_context=TransportContext.internal()))
        assert model.route_calls == compiler.calls == 0
        assert not executor.called.is_set()
    finally:
        runtime.close()


def test_real_attachment_metadata_blocks_unavailable_vision_without_calling_model(
    store,
):
    model = SplitModel(None, {}, "I can see a castle")
    runtime = WorkspaceRuntime(
        store,
        model,
        Compiler(None),
        Executor(None),
        lambda: TaskContext(),
        capability_router=router(),
    )
    try:
        wait(
            store,
            runtime.submit(
                "Что тут?",
                attachments=(WorkspaceAttachment("image", "photo.png"),),
                transport_context=TransportContext.internal(),
            ),
        )
        assert model.route_calls == model.chat_calls == 0
        assert store.list_messages()[-1].kind is MessageKind.INPUT_UNAVAILABLE
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "text",
    [
        "Find documents based on this photo",
        "Найди документы по этой фотографии",
        "Describe this photo and find my PDF files",
    ],
)
def test_unavailable_attachment_blocks_actions_until_separate_text_request(store, text):
    model = SplitModel(
        ModelTurn(
            ModelTurnKind.ACTION,
            intent_payload={"operations": [{"kind": "search_documents"}]},
        ),
        {
            "kind": "mixed",
            "language": "en",
            "confidence": 0.99,
            "conversation_text": "Describe this photo",
            "action_text": "find my PDF files",
        },
        "I see a castle",
    )
    compiler = Compiler(SimpleNamespace(state=CompilationState.READY, plan=object()))
    executor = Executor(completed_result())
    runtime = WorkspaceRuntime(
        store,
        model,
        compiler,
        executor,
        lambda: TaskContext(locale="en"),
        turn_router=TurnRouter(model),
        capability_router=router(),
    )
    try:
        wait(
            store,
            runtime.submit(
                text,
                attachments=(WorkspaceAttachment("image", "photo.png"),),
                transport_context=TransportContext.internal(),
            ),
        )
        assert not executor.called.is_set()
        assert model.route_calls == model.chat_calls == compiler.calls == 0
        assert store.list_messages()[-1].kind is MessageKind.INPUT_UNAVAILABLE
        model.classification = {
            "kind": "action",
            "language": "en",
            "confidence": 0.99,
            "conversation_text": None,
            "action_text": "find my PDF files",
        }
        wait(
            store,
            runtime.submit(
                "find my PDF files", transport_context=TransportContext.internal()
            ),
        )
        assert executor.called.is_set()
        assert all("castle" not in message.content for message in store.list_messages())
    finally:
        runtime.close()


def test_operation_outside_user_evidence_is_rejected(store):
    text = "Find my PDFs"
    model = SplitModel(
        ModelTurn(
            ModelTurnKind.ACTION,
            intent_payload={"operations": [{"kind": "copy_results"}]},
        ),
        {
            "kind": "action",
            "language": "en",
            "confidence": 1,
            "conversation_text": None,
            "action_text": text,
        },
    )
    executor = Executor(None)
    runtime = WorkspaceRuntime(
        store,
        model,
        Compiler(None),
        executor,
        lambda: TaskContext(locale="en"),
        turn_router=TurnRouter(model),
        capability_router=router(),
    )
    try:
        wait(store, runtime.submit(text, transport_context=TransportContext.internal()))
        assert not executor.called.is_set()
        assert store.list_messages()[-1].kind is MessageKind.CLARIFICATION
    finally:
        runtime.close()


def test_clarification_survives_store_reopen_and_is_principal_scoped_once(store):
    msg = store.append_message(
        MessageRole.USER, MessageKind.CONVERSATION, "copy to Private"
    )
    store.save_clarification(
        "alice", msg.message_id, {"text": msg.content, "collection": None}
    )
    reopened = WorkspaceStore(store.database)
    assert reopened.take_clarification("bob") is None
    assert reopened.take_clarification("alice")["text"] == msg.content
    assert reopened.take_clarification("alice") is None


def test_clarification_expires(store):
    now = datetime.now(UTC)
    store._now_fn = lambda: now
    msg = store.append_message(
        MessageRole.USER, MessageKind.CONVERSATION, "copy to Private"
    )
    store.save_clarification("alice", msg.message_id, {"text": msg.content})
    store._now_fn = lambda: now + timedelta(minutes=11)
    assert store.take_clarification("alice") is None


def test_stale_clarification_cannot_replace_or_resume_newer_messages(store):
    original = store.append_message(
        MessageRole.USER, MessageKind.CONVERSATION, "copy to Private"
    )
    newer = store.append_message(MessageRole.USER, MessageKind.CONVERSATION, "hello")
    store.save_clarification("alice", original.message_id, {"text": original.content})
    assert store.take_clarification("alice", newer.message_id) is None
    store.save_clarification("alice", newer.message_id, {"text": newer.content})
    store.append_message(MessageRole.USER, MessageKind.CONVERSATION, "new topic")
    reply = store.append_message(MessageRole.USER, MessageKind.CONVERSATION, "Desktop")
    assert store.take_clarification("alice", reply.message_id) is None


@pytest.mark.parametrize(
    "reply",
    [
        "Отлично, я отфильтровал результаты.",
        "I have copied your files.",
        "Я создал папку.",
    ],
)
def test_chat_cannot_present_common_fabricated_completion_as_fact(reply):
    assert safe_chat_reply(reply, "ru") != reply


def test_module_request_evidence_handles_typos_not_topics_or_quotes():
    routes = router()
    assert routes.requested_operations("fynd local files") == ("search_documents",)
    assert routes.requested_operations("PDF documents are interesting") == ()
    assert routes.requested_operations('Explain "find local files"') == ()
    assert routes.requested_operations("do not find local files") == ()


@pytest.mark.parametrize(
    "reply, resumes",
    [
        ("On my Desktop", True),
        ("No, cancel it.", False),
        ("hello", False),
        ("Do not copy to Desktop", False),
    ],
)
def test_destination_followup_is_not_approval_and_does_not_replay_on_new_topic(
    store, reply, resumes
):
    original = store.append_message(
        MessageRole.USER, MessageKind.CONVERSATION, "Copy those files to Private"
    )
    store.save_clarification(
        "core", original.message_id, {"text": original.content, "collection": None}
    )
    model = SplitModel(
        ModelTurn(
            ModelTurnKind.CLARIFICATION,
            "Which folder?",
            clarification_key="copy_destination",
        ),
        {
            "kind": "action",
            "language": "en",
            "confidence": 1,
            "conversation_text": None,
            "action_text": "placeholder",
        },
    )
    executor = Executor(None)
    runtime = WorkspaceRuntime(
        store,
        model,
        Compiler(None),
        executor,
        lambda: TaskContext(locale="en"),
        turn_router=TurnRouter(model),
        capability_router=router(),
    )
    try:
        wait(
            store, runtime.submit(reply, transport_context=TransportContext.internal())
        )
        assert not executor.called.is_set()
        assert model.route_calls == 0
        if resumes:
            assert store.list_messages()[-1].kind is MessageKind.CLARIFICATION
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "reply, expected_role",
    [
        ("Quickly: On my Desktop", "desktop"),
        ("Please put it in Documents", "documents"),
        ("Давай на рабочем столе", "desktop"),
        ("Лучше в загрузках, пожалуйста", "downloads"),
    ],
)
def test_saved_copy_draft_resumes_without_reclassifying_or_calling_model(
    store, reply, expected_role
):
    source = "Copy those results to a folder called Private"
    draft = {
        "schema_version": 1,
        "language": "en",
        "summary": source,
        "confidence": 0.9,
        "operations": [
            {
                "id": "op_1_copy_results",
                "kind": "copy_results",
                "arguments": {
                    "results_from": "context.active_results",
                    "directory_name": "Private",
                },
                "depends_on": [],
                "evidence": [source],
            }
        ],
    }
    model = SplitModel(
        ModelTurn(
            ModelTurnKind.CLARIFICATION,
            "Where should the folder go?",
            clarification_key="copy_destination",
            pending_intent_payload=draft,
        ),
        {
            "kind": "action",
            "language": "en",
            "confidence": 1,
            "conversation_text": None,
            "action_text": source,
        },
    )

    class CapturingCompiler(Compiler):
        def compile_payload(self, payload, *, text, context):
            self.payload = payload
            self.text = text
            return super().compile_payload(payload, text=text, context=context)

    approval = ApprovalRequest(
        "approval-1",
        "plan-1",
        "step-copy",
        "copy",
        expected_role,
        3,
        30,
        ("one.pdf",),
        300,
    )
    pending_result = OrchestrationResult(
        "task-1",
        "plan-1",
        OrchestrationState.AWAITING_APPROVAL,
        (),
        approval_request=approval,
    )
    compiler = CapturingCompiler(
        SimpleNamespace(state=CompilationState.READY, plan=object())
    )
    executor = Executor(pending_result)
    runtime = WorkspaceRuntime(
        store,
        model,
        compiler,
        executor,
        lambda: TaskContext(active_collection_id="collection-1", locale="en"),
        turn_router=TurnRouter(model),
        capability_router=router(),
    )
    try:
        first = wait(
            store, runtime.submit(source, transport_context=TransportContext.internal())
        )
        assert first.stage.value == "completed"
        second = wait(
            store, runtime.submit(reply, transport_context=TransportContext.internal())
        )
        assert second.stage.value == "awaiting_approval"
        assert model.classify_calls == model.route_calls == 1
        assert (
            compiler.payload["operations"][0]["arguments"]["destination"]
            == expected_role
        )
        assert compiler.text == source + " " + reply
        assert executor.called.is_set()
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "reply",
    ["not Desktop", "не на рабочем столе", "Desktop or Downloads"],
)
def test_ambiguous_or_negated_destination_never_resumes_saved_copy(store, reply):
    original = store.append_message(
        MessageRole.USER, MessageKind.CONVERSATION, "Copy results to Private"
    )
    store.save_clarification(
        "core",
        original.message_id,
        {"text": original.content, "collection": None, "intent_payload": {}},
    )
    model = SplitModel(
        ModelTurn(ModelTurnKind.CONVERSATION, "Please clarify."),
        {
            "kind": "conversation",
            "language": "en",
            "confidence": 1,
            "conversation_text": reply,
            "action_text": None,
        },
        "Please clarify.",
    )
    executor = Executor(None)
    runtime = WorkspaceRuntime(
        store,
        model,
        Compiler(None),
        executor,
        lambda: TaskContext(locale="en"),
        turn_router=TurnRouter(model),
        capability_router=router(),
    )
    try:
        wait(
            store, runtime.submit(reply, transport_context=TransportContext.internal())
        )
        assert not executor.called.is_set()
    finally:
        runtime.close()
