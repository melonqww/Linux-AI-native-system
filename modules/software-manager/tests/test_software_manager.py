from __future__ import annotations

import json
import shutil
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from ai_native_software import (
    BackendProgress,
    InstallPreferences,
    SnapdClient,
    SnapdError,
    SoftwareManager,
    SoftwareTaskStore,
    list_applications,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 25, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.value += timedelta(seconds=1)
        return self.value


class FakeBackend:
    def __init__(self) -> None:
        self.starts: list[tuple[str, str, str]] = []
        self.aborts: list[str] = []
        self.progress_by_id: dict[str, BackendProgress] = {}

    def start(self, action: str, package_name: str, *, channel: str = "stable") -> str:
        self.starts.append((action, package_name, channel))
        change_id = str(len(self.starts))
        self.progress_by_id[change_id] = BackendProgress("running", "downloading", 0)
        return change_id

    def progress(self, change_id: str) -> BackendProgress:
        return self.progress_by_id[change_id]

    def abort(self, change_id: str) -> None:
        self.aborts.append(change_id)


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

    def test_pause_and_resume_survive_manager_restart(self) -> None:
        running = self.manager.confirm(self.manager.prepare_install("discord").task_id)
        paused = self.manager.pause(running.task_id)

        self.assertEqual(paused.state, "paused")
        self.assertEqual(self.backend.aborts, ["1"])
        self.assertTrue(paused.can_resume)

        restarted = SoftwareManager(
            SoftwareTaskStore(self.root / "tasks.sqlite3"),
            self.backend,
            now_fn=self.clock,
        )
        restored = restarted.get(paused.task_id)
        self.assertEqual(restored.state, "paused")

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

        self.assertEqual(interrupted.state, "interrupted")
        self.assertTrue(interrupted.can_resume)

    def test_remove_can_be_canceled_but_not_paused(self) -> None:
        running = self.manager.confirm(self.manager.prepare_remove("spotify").task_id)

        self.assertFalse(running.can_pause)
        with self.assertRaisesRegex(ValueError, "task_cannot_pause"):
            self.manager.pause(running.task_id)

        canceled = self.manager.cancel(running.task_id)
        self.assertEqual(canceled.state, "canceled")
        self.assertEqual(self.backend.aborts, ["1"])

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
