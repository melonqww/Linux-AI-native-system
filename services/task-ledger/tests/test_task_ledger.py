import json
import shutil
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from ai_native_ledger import (
    CancellationRequested,
    InvalidTransitionError,
    ItemOutcome,
    TaskLedger,
    TaskState,
)


class TaskLedgerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[3] / "tmp" / "task-ledger" / str(uuid4())
        self.root.mkdir(parents=True)
        self.database = self.root / "ledger.sqlite3"
        self.ledger = TaskLedger(self.database)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_records_human_activity_without_diagnostics(self):
        good = self.root / "math.pdf"
        broken = self.root / "broken.pdf"
        good.write_bytes(b"pdf")
        task = self.ledger.create("documents.scan", total_items=2)
        self.ledger.start(task.task_id)
        self.ledger.record_item(
            task.task_id, outcome=ItemOutcome.SUCCEEDED, locator=good
        )
        self.ledger.record_item(
            task.task_id, outcome=ItemOutcome.SKIPPED, locator=broken
        )

        result = self.ledger.complete(task.task_id)

        self.assertEqual(result.state, TaskState.COMPLETED_WITH_SKIPS)
        self.assertEqual((result.processed_count, result.succeeded_count, result.skipped_count), (2, 1, 1))
        self.assertEqual(result.references[1].display_name, "broken.pdf")
        self.assertFalse(result.references[1].available)
        json.dumps(asdict(result), ensure_ascii=False)
        serialized = json.dumps(result, default=str)
        self.assertNotIn("error", serialized.lower())
        self.assertNotIn("trace", serialized.lower())
        with sqlite3.connect(self.database) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(task_events)")}
        self.assertEqual(columns, {"sequence", "task_id", "event", "occurred_at"})

    def test_cancel_is_cooperative_and_terminal(self):
        task = self.ledger.create("documents.scan", resumable=True)
        self.ledger.start(task.task_id)

        requested = self.ledger.request_cancel(task.task_id)

        self.assertTrue(requested.cancel_requested)
        self.assertFalse(requested.can_cancel)
        with self.assertRaises(CancellationRequested):
            self.ledger.raise_if_cancelled(task.task_id)
        cancelled = self.ledger.cancelled(task.task_id)
        self.assertEqual(cancelled.state, TaskState.CANCELLED)
        self.assertFalse(cancelled.can_continue)
        with self.assertRaises(InvalidTransitionError):
            self.ledger.request_continue(task.task_id)

    def test_system_interruption_resumes_from_persisted_checkpoint(self):
        task = self.ledger.create("documents.ocr", resumable=True)
        self.ledger.start(task.task_id)
        self.ledger.save_checkpoint(task.task_id, {"document": 4, "page": 17}, version=2)

        reopened = TaskLedger(self.database)
        self.assertEqual(reopened.recover_after_restart(), (task.task_id,))
        interrupted = reopened.get(task.task_id)
        self.assertEqual(interrupted.state, TaskState.INTERRUPTED)
        self.assertTrue(interrupted.can_continue)

        requested = reopened.request_continue(task.task_id)
        self.assertTrue(requested.continue_requested)
        self.assertFalse(requested.can_continue)
        resume = reopened.claim_continue(task.task_id)

        self.assertEqual(resume.checkpoint_version, 2)
        self.assertEqual(resume.checkpoint, {"document": 4, "page": 17})
        self.assertEqual(reopened.get(task.task_id).state, TaskState.RUNNING)

    def test_restart_without_safe_checkpoint_fails_instead_of_guessing(self):
        task = self.ledger.create("documents.scan", resumable=True)
        self.ledger.start(task.task_id)

        self.ledger.recover_after_restart()

        recovered = self.ledger.get(task.task_id)
        self.assertEqual(recovered.state, TaskState.FAILED)
        self.assertFalse(recovered.can_continue)

    def test_retention_removes_only_old_terminal_tasks(self):
        old = self.ledger.create("documents.scan")
        self.ledger.start(old.task_id)
        self.ledger.complete(old.task_id)
        active = self.ledger.create("documents.ocr", resumable=True)
        self.ledger.start(active.task_id)
        self.ledger.save_checkpoint(active.task_id, {"page": 1})
        stale = (datetime.now(UTC) - timedelta(days=8)).isoformat()
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE tasks SET finished_at = ?, updated_at = ? WHERE task_id = ?",
                (stale, stale, old.task_id),
            )

        removed = self.ledger.purge_expired()

        self.assertEqual(removed, 1)
        self.assertEqual(self.ledger.get(active.task_id).state, TaskState.RUNNING)

    def test_concurrent_progress_is_serialized_without_lost_updates(self):
        task = self.ledger.create("documents.scan", total_items=40)
        self.ledger.start(task.task_id)

        def record(index):
            self.ledger.record_item(
                task.task_id,
                outcome=ItemOutcome.SUCCEEDED,
                locator=self.root / f"{index}.pdf",
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(record, range(40)))

        result = self.ledger.complete(task.task_id)
        self.assertEqual(result.processed_count, 40)
        self.assertEqual(result.succeeded_count, 40)
        self.assertEqual(len(result.references), 40)

    def test_only_machine_tokens_and_bounded_checkpoints_are_accepted(self):
        with self.assertRaises(ValueError):
            self.ledger.create("User visible text with spaces")
        task = self.ledger.create("documents.ocr", resumable=True)
        self.ledger.start(task.task_id)
        with self.assertRaises(ValueError):
            self.ledger.save_checkpoint(task.task_id, {"payload": "x" * (65 * 1024)})
        with self.assertRaises(ValueError):
            self.ledger.save_checkpoint(task.task_id, {"token": "must-not-be-stored"})
        with self.assertRaises(ValueError):
            self.ledger.add_reference(task.task_id, locator="relative.pdf")

    def test_reference_can_be_attached_without_changing_progress(self):
        task = self.ledger.create("files.copy")
        self.ledger.start(task.task_id)

        reference = self.ledger.add_reference(
            task.task_id,
            locator=self.root / "found.pdf",
            kind="found-file",
        )

        view = self.ledger.get(task.task_id)
        self.assertEqual(view.processed_count, 0)
        self.assertEqual(view.references, (reference,))


if __name__ == "__main__":
    unittest.main()
