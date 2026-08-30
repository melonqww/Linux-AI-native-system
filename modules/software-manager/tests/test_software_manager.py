from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from ai_native_software import (
    ApplicationLifecycleManager,
    BackendProgress,
    BackupManager,
    BackupStore,
    InstallPreferences,
    LinuxDesktopNotifier,
    NetworkManagerMonitor,
    NotificationDeliveryError,
    RecoveryWorker,
    RemovalPreferences,
    RuntimeStatus,
    SnapshotSummary,
    SnapdClient,
    SnapdError,
    SnapdProvider,
    SoftwareManager,
    SoftwareTaskStore,
    list_applications,
)
from ai_native_software import worker_api


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 25, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.value += timedelta(seconds=1)
        return self.value


class ManualClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 25, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **changes: int) -> None:
        self.value += timedelta(**changes)


class FakeBackend:
    def __init__(self) -> None:
        self.starts: list[tuple[str, str, str]] = []
        self.purges: list[bool] = []
        self.aborts: list[str] = []
        self.progress_by_id: dict[str, BackendProgress] = {}

    def start(
        self,
        action: str,
        package_name: str,
        *,
        channel: str = "stable",
        purge: bool = False,
    ) -> str:
        self.starts.append((action, package_name, channel))
        self.purges.append(purge)
        change_id = str(len(self.starts))
        self.progress_by_id[change_id] = BackendProgress("running", "downloading", 0)
        return change_id

    def progress(self, change_id: str) -> BackendProgress:
        return self.progress_by_id[change_id]

    def abort(self, change_id: str) -> None:
        self.aborts.append(change_id)

    def is_retryable_error(self, error_code: str | None) -> bool:
        return error_code in {"snapd_unavailable", "network-timeout"}


class FakeConnectivity:
    def __init__(self, online: bool | None = True) -> None:
        self.online = online

    def is_online(self) -> bool | None:
        return self.online


class FakeNotifier:
    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.sent: list[tuple[str, str]] = []

    def notify(self, title: str, body: str) -> None:
        if self.fails:
            raise NotificationDeliveryError("desktop_notification_delivery_failed")
        self.sent.append((title, body))


class FakeResponse:
    def __init__(self, status: int, payload: dict[str, object]) -> None:
        self.status = status
        self.payload = json.dumps(payload).encode()

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


class FakeConnection:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, bytes | None, dict[str, str]]] = []
        self.closed = False

    def request(self, method, path, body=None, headers=None) -> None:
        self.requests.append((method, path, body, headers or {}))

    def getresponse(self) -> FakeResponse:
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


class SoftwareManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / "tmp" / "software-manager-tests" / str(uuid4())
        self.root.mkdir(parents=True)
        self.clock = Clock()
        self.backend = FakeBackend()
        self.store = SoftwareTaskStore(self.root / "tasks.sqlite3")
        self.manager = SoftwareManager(
            self.store,
            self.backend,
            now_fn=self.clock,
            id_fn=lambda: "task-1",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_catalog_contains_twenty_unique_popular_applications(self) -> None:
        applications = list_applications()

        self.assertEqual(len(applications), 20)
        self.assertEqual(len({item.application_id for item in applications}), 20)
        self.assertEqual(len({item.package_name for item in applications}), 20)
        self.assertIn("steam", {item.package_name for item in applications})
        self.assertIn("discord", {item.package_name for item in applications})
        self.assertTrue(all(item.store_url.startswith("https://snapcraft.io/") for item in applications))
        self.assertTrue(all(item.supported_locales == ("system",) for item in applications))
        self.assertTrue(all(item.supported_install_locations == ("default",) for item in applications))

    def test_install_requires_confirmation_and_reports_progress(self) -> None:
        prepared = self.manager.prepare_install("steam")

        self.assertEqual(prepared.state, "awaiting_confirmation")
        self.assertEqual(prepared.preferences, InstallPreferences())
        self.assertTrue(prepared.requires_confirmation)
        self.assertEqual(self.backend.starts, [])

        running = self.manager.confirm(prepared.task_id)
        self.assertEqual(running.state, "running")
        self.assertEqual(self.backend.starts, [("install", "steam", "stable")])

        self.backend.progress_by_id["1"] = BackendProgress("running", "downloading", 47)
        progress = self.manager.refresh(running.task_id)
        self.assertEqual(progress.progress_percent, 47)
        self.assertTrue(progress.can_pause)

        self.backend.progress_by_id["1"] = BackendProgress("completed", "completed", 100)
        completed = self.manager.refresh(running.task_id)
        self.assertEqual(completed.state, "completed")
        self.assertEqual(completed.progress_percent, 100)
        self.assertFalse(completed.can_cancel)

    def test_worker_contract_cannot_skip_or_spoof_install_confirmation(self) -> None:
        worker_api._manager = self.manager
        try:
            prepared = worker_api.worker_invoke("prepare", {
                "action": "install",
                "application_id": "steam",
                "locale": "system",
                "install_location": "default",
                "selected_options": [],
            })["task"]
            self.assertEqual(prepared["state"], "awaiting_confirmation")
            self.assertEqual(self.backend.starts, [])
            with self.assertRaisesRegex(ValueError, "task_action_mismatch"):
                worker_api.worker_invoke("respond", {
                    "task_id": prepared["task_id"],
                    "confirmed": True,
                    "action": "remove",
                    "final_confirmation": False,
                })

            running = worker_api.worker_invoke("respond", {
                "task_id": prepared["task_id"],
                "confirmed": True,
                "action": "install",
                "final_confirmation": False,
            })["task"]
            self.assertEqual(running["state"], "running")
            self.assertEqual(self.backend.starts, [("install", "steam", "stable")])
        finally:
            worker_api._manager = None

    def test_worker_contract_requires_second_removal_confirmation(self) -> None:
        worker_api._manager = self.manager
        try:
            prepared = worker_api.worker_invoke("prepare", {
                "action": "remove",
                "application_id": "spotify",
                "create_backup": False,
            })["task"]
            staged = worker_api.worker_invoke("respond", {
                "task_id": prepared["task_id"],
                "confirmed": True,
                "action": "remove",
                "final_confirmation": False,
            })["task"]
            self.assertEqual(staged["state"], "awaiting_final_confirmation")
            self.assertEqual(self.backend.starts, [])
            with self.assertRaisesRegex(ValueError, "confirmation_stage_mismatch"):
                worker_api.worker_invoke("respond", {
                    "task_id": prepared["task_id"],
                    "confirmed": True,
                    "action": "remove",
                    "final_confirmation": False,
                })

            running = worker_api.worker_invoke("respond", {
                "task_id": prepared["task_id"],
                "confirmed": True,
                "action": "remove",
                "final_confirmation": True,
            })["task"]
            self.assertEqual(running["state"], "running")
            self.assertEqual(self.backend.purges, [True])
        finally:
            worker_api._manager = None

    def test_worker_restore_reinstalls_before_snapshot_data(self) -> None:
        backup_backend = FakeSnapshotBackend()
        backup_backend.snapshots = (
            SnapshotSummary(35, "steam", "2026-08-25T12:00:00Z", 2048, True),
        )
        backups = BackupManager(
            BackupStore(self.root / "backups.sqlite3"),
            backup_backend,
            now_fn=self.clock,
        )
        backup = backups.sync()[0]
        worker_api._manager = self.manager
        worker_api._backups = backups
        try:
            result = worker_api.worker_invoke(
                "restore", {"backup_id": backup.backup_id}
            )
        finally:
            worker_api._manager = None
            worker_api._backups = None

        self.assertEqual(result["task"]["state"], "running")
        self.assertEqual(result["backup"]["state"], "reinstalling")
        self.assertEqual(
            result["task"]["preferences"]["selected_options"],
            ("pin_to_gnome", "restore_from_backup"),
        )
        self.assertEqual(backup_backend.restore_calls, [])

    def test_download_speed_eta_and_completion_notification_are_durable(self) -> None:
        clock = ManualClock()
        manager = SoftwareManager(
            self.store,
            self.backend,
            now_fn=clock,
            id_fn=lambda: "metrics-task",
        )
        running = manager.confirm(manager.prepare_install("steam").task_id)
        megabyte = 1024 * 1024

        clock.advance(seconds=10)
        self.backend.progress_by_id[running.external_id] = BackendProgress(
            "running",
            "downloading",
            10,
            downloaded_bytes=10 * megabyte,
            total_bytes=100 * megabyte,
        )
        first_sample = manager.refresh(running.task_id)
        self.assertIsNone(first_sample.download_speed_bps)

        clock.advance(seconds=10)
        self.backend.progress_by_id[running.external_id] = BackendProgress(
            "running",
            "downloading",
            30,
            downloaded_bytes=30 * megabyte,
            total_bytes=100 * megabyte,
        )
        measured = manager.refresh(running.task_id)
        self.assertEqual(measured.download_speed_bps, 2 * megabyte)
        self.assertEqual(measured.eta_seconds, 35)

        clock.advance(seconds=1)
        self.backend.progress_by_id[running.external_id] = BackendProgress(
            "completed", "completed", 100
        )
        completed = manager.refresh(running.task_id)
        self.assertTrue(completed.completion_notification_pending)
        self.assertIsNone(completed.eta_seconds)
        self.assertEqual(
            completed.to_dict()["notification"]["kind"],
            "software_install_completed",
        )

        restarted = SoftwareManager(
            SoftwareTaskStore(self.root / "tasks.sqlite3"),
            self.backend,
            now_fn=clock,
        )
        self.assertTrue(restarted.get(running.task_id).completion_notification_pending)
        acknowledged = restarted.acknowledge_notification(running.task_id)
        self.assertFalse(acknowledged.completion_notification_pending)
        self.assertIsNone(acknowledged.to_dict()["notification"])

    def test_linux_notification_is_acknowledged_only_after_delivery(self) -> None:
        notifier = FakeNotifier()
        manager = SoftwareManager(
            self.store,
            self.backend,
            now_fn=self.clock,
            id_fn=lambda: "notification-task",
            notifier=notifier,
        )
        running = manager.confirm(manager.prepare_install("steam").task_id)
        self.backend.progress_by_id[running.external_id] = BackendProgress(
            "completed", "completed", 100
        )

        completed = manager.refresh(running.task_id)

        self.assertEqual(
            notifier.sent,
            [("Steam — установка завершена", "Приложение готово к запуску.")],
        )
        self.assertFalse(completed.completion_notification_pending)

        failing = FakeNotifier(fails=True)
        retry_manager = SoftwareManager(
            self.store,
            self.backend,
            now_fn=self.clock,
            id_fn=lambda: "retry-notification-task",
            notifier=failing,
        )
        retry_running = retry_manager.confirm(
            retry_manager.prepare_install("vlc").task_id
        )
        self.backend.progress_by_id[retry_running.external_id] = BackendProgress(
            "completed", "completed", 100
        )
        pending = retry_manager.refresh(retry_running.task_id)
        self.assertTrue(pending.completion_notification_pending)

    def test_linux_notifier_uses_notify_send_without_shell(self) -> None:
        calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

        def runner(argv, **kwargs):
            calls.append((tuple(argv), kwargs))
            return subprocess.CompletedProcess(argv, 0)

        notifier = LinuxDesktopNotifier("/usr/bin/notify-send", runner=runner)
        notifier.notify("Steam — установка завершена", "Готово")

        argv, kwargs = calls[0]
        self.assertEqual(argv[0], "/usr/bin/notify-send")
        self.assertIn("--category=transfer.complete", argv)
        self.assertEqual(argv[-2:], ("Steam — установка завершена", "Готово"))
        self.assertNotIn("shell", kwargs)

    def test_pause_and_resume_survive_manager_restart(self) -> None:
        running = self.manager.confirm(self.manager.prepare_install("discord").task_id)
        pausing = self.manager.pause(running.task_id)

        self.assertEqual(pausing.state, "pausing")
        self.assertEqual(self.backend.aborts, ["1"])
        self.assertFalse(pausing.can_resume)

        self.backend.progress_by_id[running.external_id] = BackendProgress(
            "interrupted", "interrupted", 0
        )
        paused = self.manager.refresh(running.task_id)
        self.assertEqual(paused.state, "paused")
        self.assertTrue(paused.can_resume)

        restarted = SoftwareManager(
            SoftwareTaskStore(self.root / "tasks.sqlite3"),
            self.backend,
            now_fn=self.clock,
        )
        restored = restarted.get(paused.task_id)
        self.assertEqual(restored.state, "paused")
        self.assertEqual(restored.pause_reason, "user")
        self.assertFalse(restored.auto_resume)

        restarted.reconcile()
        self.assertEqual(len(self.backend.starts), 1)

        resumed = restarted.resume(restored.task_id)
        self.assertEqual(resumed.state, "running")
        self.assertEqual(resumed.external_id, "2")
        self.assertEqual(self.backend.starts[-1], ("install", "discord", "stable"))

    def test_running_change_is_reconciled_after_restart(self) -> None:
        running = self.manager.confirm(self.manager.prepare_install("firefox").task_id)
        restarted = SoftwareManager(
            SoftwareTaskStore(self.root / "tasks.sqlite3"),
            self.backend,
            now_fn=self.clock,
        )
        self.backend.progress_by_id[running.external_id] = BackendProgress(
            "completed",
            "completed",
            100,
        )

        completed = restarted.refresh(running.task_id)

        self.assertEqual(completed.state, "completed")
        self.assertEqual(completed.external_id, "1")

    def test_aborted_change_after_reboot_becomes_resumable(self) -> None:
        running = self.manager.confirm(self.manager.prepare_install("thunderbird").task_id)
        self.backend.progress_by_id[running.external_id] = BackendProgress(
            "interrupted",
            "interrupted",
            31,
        )

        interrupted = self.manager.refresh(running.task_id)

        self.assertEqual(interrupted.state, "retry_wait")
        self.assertTrue(interrupted.auto_resume)
        self.assertTrue(interrupted.can_resume)

    def test_remove_can_be_canceled_but_not_paused(self) -> None:
        prepared = self.manager.prepare_remove("spotify")
        final_confirmation = self.manager.confirm(prepared.task_id)

        self.assertEqual(final_confirmation.state, "awaiting_final_confirmation")
        self.assertTrue(final_confirmation.requires_final_confirmation)
        self.assertEqual(self.backend.starts, [])

        running = self.manager.confirm_removal(final_confirmation.task_id)

        self.assertFalse(running.can_pause)
        with self.assertRaisesRegex(ValueError, "task_cannot_pause"):
            self.manager.pause(running.task_id)

        canceling = self.manager.cancel(running.task_id)
        self.assertEqual(canceling.state, "canceling")
        self.backend.progress_by_id[running.external_id] = BackendProgress(
            "interrupted", "interrupted", 0
        )
        canceled = self.manager.refresh(running.task_id)
        self.assertEqual(canceled.state, "canceled")
        self.assertEqual(self.backend.aborts, ["1", "1"])
        self.assertEqual(self.backend.purges, [False])

    def test_offline_download_stops_and_resumes_automatically_without_duplicate(self) -> None:
        clock = ManualClock()
        connectivity = FakeConnectivity(True)
        manager = SoftwareManager(
            self.store,
            self.backend,
            now_fn=clock,
            id_fn=lambda: "offline-task",
            connectivity=connectivity,
        )
        running = manager.confirm(manager.prepare_install("steam").task_id)
        self.backend.progress_by_id[running.external_id] = BackendProgress(
            "running", "downloading", 42
        )
        manager.refresh(running.task_id)

        connectivity.online = False
        stopping = manager.refresh(running.task_id)
        self.assertEqual(stopping.state, "pausing")
        self.assertEqual(stopping.pause_reason, "offline")
        self.assertEqual(stopping.progress_percent, 42)

        self.backend.progress_by_id[running.external_id] = BackendProgress(
            "interrupted", "interrupted", 42
        )
        waiting = manager.refresh(running.task_id)
        self.assertEqual(waiting.state, "waiting_for_network")
        self.assertTrue(waiting.auto_resume)
        self.assertTrue(waiting.can_pause)

        restarted = SoftwareManager(
            SoftwareTaskStore(self.root / "tasks.sqlite3"),
            self.backend,
            now_fn=clock,
            connectivity=connectivity,
        )
        persisted = restarted.get(waiting.task_id)
        self.assertEqual(persisted.pause_reason, "offline")
        self.assertTrue(persisted.auto_resume)

        connectivity.online = True
        resumed = restarted.reconcile()[0]
        self.assertEqual(resumed.state, "running")
        self.assertEqual(resumed.external_id, "2")
        self.assertEqual(resumed.progress_percent, 42)
        self.assertEqual(len(self.backend.starts), 2)

        restarted.reconcile()
        self.assertEqual(len(self.backend.starts), 2)

    def test_user_pause_while_offline_disables_automatic_resume(self) -> None:
        connectivity = FakeConnectivity(False)
        manager = SoftwareManager(
            self.store,
            self.backend,
            now_fn=self.clock,
            id_fn=lambda: "manual-offline-pause",
            connectivity=connectivity,
        )
        waiting = manager.confirm(manager.prepare_install("steam").task_id)
        self.assertEqual(waiting.state, "waiting_for_network")
        self.assertEqual(self.backend.starts, [])

        paused = manager.pause(waiting.task_id)
        self.assertEqual(paused.state, "paused")
        self.assertEqual(paused.pause_reason, "user")
        self.assertFalse(paused.auto_resume)

        connectivity.online = True
        reconciled = manager.reconcile()[0]
        self.assertEqual(reconciled.state, "paused")
        self.assertEqual(self.backend.starts, [])

    def test_restart_reuses_existing_snap_change_instead_of_starting_duplicate(self) -> None:
        running = self.manager.confirm(self.manager.prepare_install("firefox").task_id)
        self.backend.progress_by_id[running.external_id] = BackendProgress(
            "running", "downloading", 38
        )
        restarted = SoftwareManager(
            SoftwareTaskStore(self.root / "tasks.sqlite3"),
            self.backend,
            now_fn=self.clock,
        )

        reconciled = restarted.reconcile()[0]

        self.assertEqual(reconciled.state, "running")
        self.assertEqual(reconciled.external_id, running.external_id)
        self.assertEqual(reconciled.progress_percent, 38)
        self.assertEqual(len(self.backend.starts), 1)

    def test_stalled_download_is_stopped_before_retry(self) -> None:
        clock = ManualClock()
        manager = SoftwareManager(
            self.store,
            self.backend,
            now_fn=clock,
            id_fn=lambda: "stalled-task",
            stalled_timeout_seconds=30,
        )
        running = manager.confirm(manager.prepare_install("steam").task_id)
        clock.advance(seconds=31)
        self.backend.progress_by_id[running.external_id] = BackendProgress(
            "running", "downloading", 0
        )

        stopping = manager.refresh(running.task_id)

        self.assertEqual(stopping.state, "pausing")
        self.assertEqual(stopping.pause_reason, "retry")
        self.assertEqual(self.backend.aborts, [running.external_id])

    def test_installing_phase_is_not_paused_when_network_status_changes(self) -> None:
        connectivity = FakeConnectivity(True)
        manager = SoftwareManager(
            self.store,
            self.backend,
            now_fn=self.clock,
            id_fn=lambda: "installing-task",
            connectivity=connectivity,
        )
        running = manager.confirm(manager.prepare_install("steam").task_id)
        self.backend.progress_by_id[running.external_id] = BackendProgress(
            "running", "installing", 85
        )
        installing = manager.refresh(running.task_id)
        connectivity.online = False

        unchanged = manager.refresh(installing.task_id)

        self.assertEqual(unchanged.state, "running")
        self.assertEqual(unchanged.phase, "installing")
        self.assertEqual(self.backend.aborts, [])

    def test_retry_wait_respects_backoff_then_restarts_once(self) -> None:
        clock = ManualClock()
        manager = SoftwareManager(
            self.store,
            self.backend,
            now_fn=clock,
            id_fn=lambda: "retry-task",
        )
        running = manager.confirm(manager.prepare_install("steam").task_id)
        self.backend.progress_by_id[running.external_id] = BackendProgress(
            "failed", "failed", 23, "network-timeout"
        )
        waiting = manager.refresh(running.task_id)
        self.assertEqual(waiting.state, "retry_wait")
        self.assertEqual(waiting.retry_count, 1)

        manager.reconcile()
        self.assertEqual(len(self.backend.starts), 1)
        clock.advance(seconds=5)
        resumed = manager.reconcile()[0]
        self.assertEqual(resumed.state, "running")
        self.assertEqual(len(self.backend.starts), 2)

    def test_network_monitor_invokes_nm_online_without_shell(self) -> None:
        calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

        def runner(argv, **kwargs):
            calls.append((tuple(argv), kwargs))
            return subprocess.CompletedProcess(argv, 0)

        monitor = NetworkManagerMonitor("/usr/bin/nm-online", runner=runner)

        self.assertTrue(monitor.is_online())
        argv, kwargs = calls[0]
        self.assertEqual(argv[0], "/usr/bin/nm-online")
        self.assertIn("--timeout=1", argv)
        self.assertNotIn("shell", kwargs)

    def test_snapd_provider_classifies_only_transient_errors_for_retry(self) -> None:
        provider = SnapdProvider(connection_factory=lambda: self.fail("connection opened"))

        self.assertTrue(provider.is_retryable_error("network-timeout"))
        self.assertTrue(provider.is_retryable_error("snapd_unavailable"))
        self.assertFalse(provider.is_retryable_error("auth-cancelled"))

    def test_recovery_worker_reconciles_immediately_and_stops_cleanly(self) -> None:
        calls: list[str] = []

        class ManagerStub:
            def reconcile(self):
                calls.append("reconcile")
                return ()

        class StopAfterTwoPasses:
            def wait(self, timeout):
                self.timeout = timeout
                return len(calls) >= 2

        stop = StopAfterTwoPasses()
        worker = RecoveryWorker(ManagerStub(), interval_seconds=1)

        worker.run_forever(stop)

        self.assertEqual(calls, ["reconcile", "reconcile"])
        self.assertEqual(stop.timeout, 1)

    def test_schema_three_database_is_migrated_without_losing_compatibility(self) -> None:
        database = self.root / "schema-three.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                """CREATE TABLE software_tasks (
                    task_id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    application_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    package_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    preferences_json TEXT NOT NULL,
                    removal_preferences_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    progress_percent INTEGER,
                    downloaded_bytes INTEGER,
                    total_bytes INTEGER,
                    download_speed_bps INTEGER,
                    eta_seconds INTEGER,
                    completion_notification_pending INTEGER NOT NULL DEFAULT 0,
                    external_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_code TEXT
                )"""
            )
            connection.execute("PRAGMA user_version = 3")

        SoftwareTaskStore(database)

        with sqlite3.connect(database) as connection:
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(software_tasks)")
            }
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertTrue({
            "pause_reason",
            "auto_resume",
            "retry_count",
            "next_retry_at",
            "last_progress_at",
        }.issubset(columns))
        self.assertEqual(version, 4)

    def test_remove_without_backup_uses_snapd_purge_after_two_confirmations(self) -> None:
        prepared = self.manager.prepare_remove(
            "spotify", RemovalPreferences(create_backup=False)
        )
        final_confirmation = self.manager.confirm(prepared.task_id)
        running = self.manager.confirm_removal(final_confirmation.task_id)

        self.assertEqual(running.state, "running")
        self.assertEqual(self.backend.purges, [True])

    def test_completed_remove_triggers_backup_discovery(self) -> None:
        synced: list[str] = []
        manager = SoftwareManager(
            self.store,
            self.backend,
            now_fn=self.clock,
            id_fn=lambda: "remove-with-backup",
            on_remove_completed=synced.append,
        )
        prepared = manager.prepare_remove("steam")
        running = manager.confirm_removal(manager.confirm(prepared.task_id).task_id)
        self.backend.progress_by_id[running.external_id] = BackendProgress(
            "completed", "completed", 100
        )

        manager.refresh(running.task_id)

        self.assertEqual(synced, ["steam"])

    def test_backend_failure_is_persisted_without_leaking_message(self) -> None:
        class BrokenBackend(FakeBackend):
            def start(self, action, package_name, *, channel="stable"):
                raise SnapdError("auth-cancelled", "private backend message")

        manager = SoftwareManager(self.store, BrokenBackend(), now_fn=self.clock, id_fn=lambda: "broken")
        failed = manager.confirm(manager.prepare_install("vlc").task_id)

        self.assertEqual(failed.state, "failed")
        self.assertEqual(failed.error_code, "auth-cancelled")
        self.assertNotIn("private backend message", repr(failed))

    def test_progress_never_moves_backwards(self) -> None:
        running = self.manager.confirm(self.manager.prepare_install("code").task_id)
        self.backend.progress_by_id["1"] = BackendProgress("running", "downloading", 70)
        self.assertEqual(self.manager.refresh(running.task_id).progress_percent, 70)
        self.backend.progress_by_id["1"] = BackendProgress("running", "installing", 20)
        installing = self.manager.refresh(running.task_id)
        self.assertEqual(installing.progress_percent, 70)
        self.assertFalse(installing.can_pause)

    def test_future_install_preferences_are_explicitly_gated_and_persisted(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported_application_locale"):
            self.manager.prepare_install("steam", InstallPreferences(locale="ru"))
        with self.assertRaisesRegex(ValueError, "unsupported_install_location"):
            self.manager.prepare_install(
                "steam",
                InstallPreferences(install_location="secondary-drive"),
            )
        with self.assertRaisesRegex(ValueError, "unsupported_install_option"):
            self.manager.prepare_install(
                "steam",
                InstallPreferences(selected_options=("desktop-shortcut",)),
            )

        prepared = self.manager.prepare_install("steam", InstallPreferences())
        restored = SoftwareTaskStore(self.root / "tasks.sqlite3").get(prepared.task_id)
        self.assertEqual(restored.preferences.to_dict(), {
            "locale": "system",
            "install_location": "default",
            "selected_options": (),
        })


class FakeSnapshotBackend:
    def __init__(self) -> None:
        self.snapshots: tuple[SnapshotSummary, ...] = ()
        self.restore_calls: list[tuple[int, str]] = []
        self.progress_by_id: dict[str, BackendProgress] = {}

    def list_snapshots(self, package_name=None):
        return tuple(
            item for item in self.snapshots
            if package_name is None or item.package_name == package_name
        )

    def restore_snapshot(self, set_id, package_name):
        self.restore_calls.append((set_id, package_name))
        self.progress_by_id["91"] = BackendProgress("running", "restoring", 0)
        return "91"

    def progress(self, change_id):
        return self.progress_by_id[change_id]


class BackupManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / "tmp" / "software-backup-tests" / str(uuid4())
        self.root.mkdir(parents=True)
        self.clock = ManualClock()
        self.backend = FakeSnapshotBackend()
        self.store = BackupStore(self.root / "tasks.sqlite3")
        self.manager = BackupManager(self.store, self.backend, now_fn=self.clock)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_automatic_backup_countdown_survives_restart(self) -> None:
        self.backend.snapshots = (
            SnapshotSummary(
                set_id=35,
                package_name="steam",
                created_at="2026-08-25T12:00:00Z",
                size_bytes=2048,
                automatic=True,
            ),
        )

        backup = self.manager.sync()[0]
        self.assertEqual(backup.to_dict(now=self.clock())["remaining_days"], 31)
        self.clock.advance(days=30, hours=23)

        restarted = BackupManager(
            BackupStore(self.root / "tasks.sqlite3"),
            self.backend,
            now_fn=self.clock,
        )
        restored = restarted.list()[0]
        self.assertEqual(restored.to_dict(now=self.clock())["remaining_days"], 1)
        self.assertEqual(restored.remaining_seconds(self.clock()), 3600)

        self.clock.advance(hours=1)
        self.backend.snapshots = ()
        self.assertEqual(restarted.sync()[0].state, "expired")

    def test_restore_has_confirmation_and_persistent_change(self) -> None:
        self.backend.snapshots = (
            SnapshotSummary(35, "steam", "2026-08-25T12:00:00Z", 2048, True),
        )
        backup = self.manager.sync()[0]

        prepared = self.manager.prepare_restore(backup.backup_id)
        self.assertEqual(prepared.state, "awaiting_restore_confirmation")
        self.assertEqual(self.backend.restore_calls, [])

        restoring = self.manager.confirm_restore(backup.backup_id)
        self.assertEqual(restoring.state, "restoring")
        self.assertEqual(self.backend.restore_calls, [(35, "steam")])
        persisted = BackupStore(self.root / "tasks.sqlite3").get(backup.backup_id)
        self.assertEqual(persisted.restore_change_id, "91")

        self.backend.progress_by_id["91"] = BackendProgress("completed", "completed", 100)
        completed = self.manager.refresh_restore(backup.backup_id)
        self.assertEqual(completed.state, "restored")
        self.assertIsNotNone(completed.last_restored_at)

    def test_restore_reinstalls_snap_before_restoring_its_data(self) -> None:
        self.backend.snapshots = (
            SnapshotSummary(35, "steam", "2026-08-25T12:00:00Z", 2048, True),
        )
        backup = self.manager.sync()[0]
        self.manager.prepare_restore(backup.backup_id)
        staged = self.manager.begin_reinstall(backup.backup_id, "restore-task")

        self.assertEqual(staged.state, "reinstalling")
        self.assertEqual(self.backend.restore_calls, [])
        self.assertEqual(
            self.manager.continue_reinstall(backup.backup_id, "running").state,
            "reinstalling",
        )

        restoring = self.manager.continue_reinstall(backup.backup_id, "completed")
        self.assertEqual(restoring.state, "restoring")
        self.assertEqual(self.backend.restore_calls, [(35, "steam")])


class FakeRuntimeAdapter:
    def __init__(self) -> None:
        self.current = RuntimeStatus("stopped")
        self.launches: list[str] = []
        self.closes: list[str] = []
        self.pins: list[tuple[str, bool]] = []

    def launch(self, desktop_id):
        self.launches.append(desktop_id)

    def status(self, desktop_id):
        return self.current

    def close(self, desktop_id):
        self.closes.append(desktop_id)

    def set_pinned(self, desktop_id, pinned):
        self.pins.append((desktop_id, pinned))
        return True


class ApplicationLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = ManualClock()
        self.adapter = FakeRuntimeAdapter()
        self.manager = ApplicationLifecycleManager(
            self.adapter,
            now_fn=self.clock,
            launch_timeout=timedelta(seconds=5),
        )

    def test_launch_is_not_reported_running_until_gnome_confirms_it(self) -> None:
        starting = self.manager.launch("steam", "steam_steam.desktop")
        self.assertEqual(starting.state, "starting")
        self.assertNotIn("close", starting.actions)

        self.clock.advance(seconds=5)
        failed = self.manager.refresh("steam")
        self.assertEqual(failed.status_label, "Не удалось запустить")
        self.assertEqual(failed.actions, ("retry",))
        self.assertNotIn("details", failed.actions)
        self.assertNotIn("open", failed.actions)

    def test_running_state_only_offers_close_and_close_is_verified(self) -> None:
        self.manager.launch("steam", "steam_steam.desktop")
        self.adapter.current = RuntimeStatus("running", window_count=1, process_count=2)
        running = self.manager.refresh("steam")

        self.assertEqual(running.status_label, "Запущено")
        self.assertEqual(running.actions, ("close",))
        stopping = self.manager.close("steam")
        self.assertEqual(stopping.state, "stopping")
        self.assertEqual(self.adapter.closes, ["steam_steam.desktop"])

        self.adapter.current = RuntimeStatus("stopped")
        self.assertEqual(self.manager.refresh("steam").state, "stopped")

    def test_pin_is_delegated_to_verified_desktop_adapter(self) -> None:
        self.manager.observe("steam", "steam_steam.desktop")
        self.assertTrue(self.manager.set_pinned("steam", True))
        self.assertEqual(self.adapter.pins, [("steam_steam.desktop", True)])

    def test_gnome_adapter_uses_shell_state_and_graceful_quit(self) -> None:
        source = (
            PROJECT_ROOT / "modules" / "software-manager" / "gnome" / "software-runtime.js"
        ).read_text(encoding="utf-8")
        self.assertIn("get_state()", source)
        self.assertIn("get_windows()", source)
        self.assertIn("request_quit()", source)
        self.assertIn("favorite-apps", source)
        self.assertNotIn("kill(", source)


class SnapdClientTests(unittest.TestCase):
    def test_start_uses_exact_package_and_polkit_interaction_header(self) -> None:
        connection = FakeConnection([
            FakeResponse(202, {"type": "async", "status-code": 202, "change": "42"})
        ])
        client = SnapdClient(connection_factory=lambda: connection)

        change_id = client.start("install", "telegram-desktop")

        self.assertEqual(change_id, "42")
        method, path, body, headers = connection.requests[0]
        self.assertEqual((method, path), ("POST", "/v2/snaps/telegram-desktop"))
        self.assertEqual(json.loads(body), {"action": "install", "channel": "stable"})
        self.assertEqual(headers["X-Allow-Interaction"], "true")
        self.assertTrue(connection.closed)

    def test_remove_purge_is_an_explicit_json_flag(self) -> None:
        connection = FakeConnection([
            FakeResponse(202, {"type": "async", "status-code": 202, "change": "43"})
        ])
        client = SnapdClient(connection_factory=lambda: connection)

        client.start("remove", "steam", purge=True)

        self.assertEqual(json.loads(connection.requests[0][2]), {"action": "remove", "purge": True})

    def test_snapshots_are_parsed_and_restore_uses_exact_set_and_package(self) -> None:
        connection = FakeConnection([
            FakeResponse(200, {
                "type": "sync",
                "result": [{
                    "id": 35,
                    "auto": True,
                    "snapshots": [{
                        "snap": "steam",
                        "time": "2026-08-25T12:00:00Z",
                        "size": 2048,
                    }],
                }],
            }),
            FakeResponse(202, {"type": "async", "change": "91"}),
        ])
        client = SnapdClient(connection_factory=lambda: connection)

        snapshots = client.list_snapshots("steam")
        change_id = client.restore_snapshot(35, "steam")

        self.assertEqual(snapshots, (
            SnapshotSummary(35, "steam", "2026-08-25T12:00:00Z", 2048, True),
        ))
        self.assertEqual(change_id, "91")
        method, path, body, headers = connection.requests[1]
        self.assertEqual((method, path), ("POST", "/v2/snapshots"))
        self.assertEqual(json.loads(body), {"action": "restore", "set": 35, "snaps": ["steam"]})
        self.assertEqual(headers["X-Allow-Interaction"], "true")

    def test_desktop_ids_are_discovered_from_installed_snap_metadata(self) -> None:
        connection = FakeConnection([
            FakeResponse(200, {
                "type": "sync",
                "result": [
                    {
                        "snap": "steam",
                        "name": "steam",
                        "desktop-file": "/var/lib/snapd/desktop/applications/steam_steam.desktop",
                    },
                    {"snap": "other", "desktop-file": "/tmp/untrusted.desktop"},
                ],
            })
        ])
        client = SnapdClient(connection_factory=lambda: connection)

        self.assertEqual(client.desktop_ids("steam"), ("steam_steam.desktop",))
        self.assertEqual(connection.requests[0][1], "/v2/apps?names=steam")

    def test_rejects_option_and_path_injection(self) -> None:
        client = SnapdClient(connection_factory=lambda: self.fail("connection opened"))
        for package_name in ("--dangerous", "../steam", "steam;reboot", "Steam"):
            with self.subTest(package_name=package_name):
                with self.assertRaisesRegex(ValueError, "invalid_snap_package_name"):
                    client.start("install", package_name)

    def test_change_progress_is_bounded_and_phase_aware(self) -> None:
        progress = SnapdClient._parse_progress({
            "status": "Doing",
            "ready": False,
            "tasks": [
                {"kind": "download-snap", "status": "Doing", "progress": {"done": 45, "total": 100}},
                {"kind": "setup-snap", "status": "Do", "progress": {"done": 0, "total": 1}},
            ],
        })

        self.assertEqual(progress.state, "running")
        self.assertEqual(progress.phase, "downloading")
        self.assertEqual(progress.progress_percent, 45)
        self.assertEqual(progress.downloaded_bytes, 45)
        self.assertEqual(progress.total_bytes, 100)

    def test_running_change_reserves_one_hundred_percent_for_ready_state(self) -> None:
        progress = SnapdClient._parse_progress({
            "status": "Doing",
            "ready": False,
            "tasks": [
                {
                    "kind": "download-snap",
                    "status": "Doing",
                    "progress": {"done": 100, "total": 100},
                },
            ],
        })

        self.assertEqual(progress.state, "running")
        self.assertEqual(progress.progress_percent, 99)

    def test_snapd_error_returns_stable_code(self) -> None:
        connection = FakeConnection([
            FakeResponse(401, {
                "type": "error",
                "status-code": 401,
                "result": {"kind": "auth-cancelled", "message": "secret detail"},
            })
        ])
        client = SnapdClient(connection_factory=lambda: connection)

        with self.assertRaises(SnapdError) as context:
            client.start("remove", "steam")
        self.assertEqual(context.exception.code, "auth-cancelled")


if __name__ == "__main__":
    unittest.main()
