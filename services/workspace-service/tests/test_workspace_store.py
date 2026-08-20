import shutil
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from ai_native_workspace import (
    MessageKind,
    MessageRole,
    WorkspaceStage,
    WorkspaceStore,
    WorkspaceTransitionError,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, **kwargs) -> None:
        self.value += timedelta(**kwargs)


class WorkspaceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / "tmp" / "workspace-tests" / str(uuid4())
        self.clock = Clock()
        self.store = WorkspaceStore(
            self.root / "workspace.sqlite3", now_fn=self.clock.now
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_tracks_public_stages_elapsed_time_and_task_link(self) -> None:
        user = self.store.append_message(
            MessageRole.USER, MessageKind.CONVERSATION, "Найди PDF по математике"
        )
        run = self.store.create_run(user.message_id)
        self.clock.advance(seconds=2)
        run = self.store.transition(run.run_id, WorkspaceStage.UNDERSTANDING)
        self.clock.advance(seconds=3)
        run = self.store.transition(run.run_id, WorkspaceStage.PLANNING)
        self.clock.advance(seconds=5)
        run = self.store.transition(run.run_id, WorkspaceStage.EXECUTING)
        self.assertEqual(
            [item.run_id for item in self.store.list_runs(active_only=True)],
            [run.run_id],
        )
        self.clock.advance(seconds=7)
        run = self.store.transition(run.run_id, WorkspaceStage.SUMMARIZING)
        task_id = str(uuid4())
        assistant = self.store.append_message(
            MessageRole.ASSISTANT,
            MessageKind.TASK_RESULT,
            "Нашёл 12 PDF-файлов.",
            task_id=task_id,
        )
        self.clock.advance(seconds=1)
        run = self.store.complete(run.run_id, assistant.message_id, task_id=task_id)

        self.assertEqual(run.stage, WorkspaceStage.COMPLETED)
        self.assertEqual(run.stage_label, "Готово")
        self.assertEqual(run.elapsed_ms, 18_000)
        self.assertEqual(run.stage_elapsed_ms, 0)
        self.assertEqual(run.task_id, task_id)
        self.assertEqual(run.assistant_message_id, assistant.message_id)
        self.assertEqual(self.store.list_runs(active_only=True), ())
        self.assertEqual(self.store.list_runs(limit=1)[0].run_id, run.run_id)
        self.assertEqual(
            [message.content for message in self.store.list_messages()],
            ["Найди PDF по математике", "Нашёл 12 PDF-файлов."],
        )

    def test_rejects_skipped_or_reversed_stages(self) -> None:
        user = self.store.append_message(
            MessageRole.USER, MessageKind.CONVERSATION, "Привет"
        )
        run = self.store.create_run(user.message_id)

        with self.assertRaises(WorkspaceTransitionError):
            self.store.transition(run.run_id, WorkspaceStage.EXECUTING)

        run = self.store.transition(run.run_id, WorkspaceStage.UNDERSTANDING)
        assistant = self.store.append_message(
            MessageRole.ASSISTANT, MessageKind.CONVERSATION, "Привет!"
        )
        run = self.store.complete(run.run_id, assistant.message_id)
        with self.assertRaises(WorkspaceTransitionError):
            self.store.transition(run.run_id, WorkspaceStage.PLANNING)

    def test_rejects_mismatched_task_result_link(self) -> None:
        user = self.store.append_message(
            MessageRole.USER, MessageKind.CONVERSATION, "Выполни задачу"
        )
        run = self.store.create_run(user.message_id)
        run = self.store.transition(run.run_id, WorkspaceStage.UNDERSTANDING)
        assistant = self.store.append_message(
            MessageRole.ASSISTANT,
            MessageKind.TASK_RESULT,
            "Готово",
            task_id=str(uuid4()),
        )

        with self.assertRaises(ValueError):
            self.store.complete(run.run_id, assistant.message_id, task_id=str(uuid4()))

    def test_messages_and_finished_runs_expire_after_one_day(self) -> None:
        user = self.store.append_message(
            MessageRole.USER, MessageKind.CONVERSATION, "Временное сообщение"
        )
        finished = self.store.create_run(user.message_id)
        finished = self.store.transition(finished.run_id, WorkspaceStage.UNDERSTANDING)
        assistant = self.store.append_message(
            MessageRole.ASSISTANT, MessageKind.CONVERSATION, "Временный ответ"
        )
        self.store.complete(finished.run_id, assistant.message_id)

        active_message = self.store.append_message(
            MessageRole.USER, MessageKind.CONVERSATION, "Активная задача"
        )
        active = self.store.create_run(active_message.message_id)
        self.store.transition(active.run_id, WorkspaceStage.UNDERSTANDING)
        self.clock.advance(hours=25)

        removed_messages, removed_runs = self.store.purge_expired()

        self.assertEqual(removed_messages, 3)
        self.assertEqual(removed_runs, 1)
        self.assertEqual(self.store.list_messages(), ())
        self.assertEqual(self.store.run(active.run_id).stage, WorkspaceStage.UNDERSTANDING)

    def test_message_contract_is_bounded(self) -> None:
        for invalid in ("", "x" * 16_001, "bad\x00value"):
            with self.subTest(invalid=invalid[:20]):
                with self.assertRaises((TypeError, ValueError)):
                    self.store.append_message(
                        MessageRole.USER, MessageKind.CONVERSATION, invalid
                    )
        for limit in (0, 501, True):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    self.store.list_messages(limit=limit)
        for limit in (0, 101, True):
            with self.subTest(run_limit=limit):
                with self.assertRaises(ValueError):
                    self.store.list_runs(limit=limit)
        with self.assertRaises(ValueError):
            self.store.list_runs(active_only="yes")


if __name__ == "__main__":
    unittest.main()
