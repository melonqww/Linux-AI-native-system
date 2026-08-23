import shutil
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[3]
for source in (
    PROJECT_ROOT / "services" / "storage-catalog" / "src",
    PROJECT_ROOT / "services" / "indexer" / "src",
    PROJECT_ROOT / "modules" / "documents-pdf" / "src",
    PROJECT_ROOT / "services" / "index-scheduler" / "src",
):
    sys.path.insert(0, str(source))

from ai_native_scheduler import (
    BackgroundIndexService,
    CoalescingEventQueue,
    EventKind,
    FileEvent,
    IndexScheduler,
    LinuxInotifyWatcher,
    ResourceBudget,
    SchedulerState,
)
from ai_native_storage import FileCatalog, PermissionLevel, VolumeRegistry
from ai_native_storage.contracts import DiscoveredVolume


class Discovery:
    def __init__(self, root: Path) -> None:
        self.root = root

    def discover(self):
        return [
            DiscoveredVolume(
                "test-volume", "Test", str(self.root), "test", "testfs", True, False, False
            )
        ]


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / "tmp" / "scheduler-tests" / str(uuid4())
        self.files = self.root / "files"
        self.files.mkdir(parents=True)
        self.storage_db = self.root / "storage.sqlite3"
        self.index_db = self.root / "index.sqlite3"
        VolumeRegistry(self.storage_db, discovery=Discovery(self.files)).refresh()
        self.scheduler = IndexScheduler(
            storage_database=self.storage_db,
            index_database=self.index_db,
            queue=CoalescingEventQueue(debounce_seconds=0),
            budget=ResourceBudget(max_batch=8, load_probe=lambda: 0),
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def event(self, path: Path, kind: EventKind) -> FileEvent:
        return FileEvent("test-volume", str(path), kind, observed_at=1)

    def test_incrementally_adds_modifies_and_removes_text(self) -> None:
        document = self.files / "notes.txt"
        document.write_text("algebra geometry", encoding="utf-8")
        self.scheduler.submit(self.event(document, EventKind.CREATED))
        self.scheduler.process_once(now=2)
        self.assertEqual(len(self.scheduler.indexer.search("algebra")), 1)

        document.write_text("topology analysis", encoding="utf-8")
        self.scheduler.submit(self.event(document, EventKind.MODIFIED))
        self.scheduler.process_once(now=3)
        self.assertEqual(self.scheduler.indexer.search("algebra"), [])
        self.assertEqual(len(self.scheduler.indexer.search("topology")), 1)

        document.unlink()
        self.scheduler.submit(self.event(document, EventKind.DELETED))
        status = self.scheduler.process_once(now=4)
        self.assertEqual(status.failed, 0)
        self.assertEqual(self.scheduler.indexer.search("topology"), [])
        self.assertEqual(FileCatalog(self.storage_db).status()["entries"], 0)

    def test_sensitive_parent_is_never_content_indexed(self) -> None:
        secret_directory = self.files / ".ssh"
        secret_directory.mkdir()
        secret = secret_directory / "notes.txt"
        secret.write_text("private material", encoding="utf-8")
        self.scheduler.submit(self.event(secret, EventKind.CREATED))
        self.scheduler.process_once(now=2)
        self.assertEqual(self.scheduler.indexer.search("private"), [])

    def test_metadata_permission_never_reads_content(self) -> None:
        VolumeRegistry(self.storage_db).set_permission("test-volume", PermissionLevel.METADATA)
        document = self.files / "metadata-only.txt"
        document.write_text("must not enter fts", encoding="utf-8")
        self.scheduler.submit(self.event(document, EventKind.CREATED))
        self.scheduler.process_once(now=2)
        self.assertEqual(self.scheduler.indexer.search("enter"), [])
        self.assertEqual(FileCatalog(self.storage_db).status()["entries"], 1)

    def test_pdf_extractor_feeds_incremental_fts(self) -> None:
        class Extractor:
            def extract(self, _path):
                return SimpleNamespace(text="[PDF page 1]\ncalculus theorem")

        document = self.files / "math.pdf"
        document.write_bytes(b"fake-pdf-for-injected-extractor")
        scheduler = IndexScheduler(
            storage_database=self.storage_db,
            index_database=self.index_db,
            queue=CoalescingEventQueue(debounce_seconds=0),
            budget=ResourceBudget(max_batch=2, load_probe=lambda: 0),
            pdf_extractor=Extractor(),
        )
        scheduler.submit(self.event(document, EventKind.CREATED))
        scheduler.process_once(now=2)
        self.assertEqual(len(scheduler.indexer.search("calculus")), 1)

    def test_pauses_without_consuming_queue_under_high_load(self) -> None:
        document = self.files / "later.txt"
        document.write_text("later", encoding="utf-8")
        scheduler = IndexScheduler(
            storage_database=self.storage_db,
            index_database=self.index_db,
            queue=CoalescingEventQueue(debounce_seconds=0),
            budget=ResourceBudget(max_batch=1, max_normalized_load=1, load_probe=lambda: 2),
        )
        scheduler.submit(self.event(document, EventKind.CREATED))
        status = scheduler.process_once(now=2)
        self.assertEqual(status.state, SchedulerState.PAUSED_LOAD)
        self.assertEqual(status.queued, 1)

    def test_catalog_rejects_event_outside_volume(self) -> None:
        outside = self.root / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        with self.assertRaises(ValueError):
            FileCatalog(self.storage_db).update_path("test-volume", outside)

    def test_rescan_is_incremental_and_reconciles_deleted_sources(self) -> None:
        for index in range(12):
            (self.files / f"document-{index}.txt").write_text(
                f"bounded crawl term {index}", encoding="utf-8"
            )
        stale = self.files / "stale.txt"
        stale.write_text("stale marker", encoding="utf-8")
        self.scheduler.submit(self.event(stale, EventKind.CREATED))
        self.scheduler.process_once(now=2)
        stale.unlink()

        self.scheduler.request_rescan("test-volume")
        first = self.scheduler.process_once(now=3)
        self.assertEqual(first.active_rescans, 1)
        self.assertLessEqual(self.scheduler.indexer.get_index_status()["sources"], 8)
        for cycle in range(4, 30):
            status = self.scheduler.process_once(now=cycle)
            if status.active_rescans == 0:
                break
        self.assertEqual(status.active_rescans, 0)
        self.assertEqual(len(self.scheduler.indexer.search("bounded", limit=20)), 12)
        self.assertEqual(self.scheduler.indexer.search("stale"), [])

    def test_revoking_permission_removes_volume_watch(self) -> None:
        class Watcher:
            def __init__(self):
                self.added = []
                self.removed = []

            def add_tree(self, volume_id, root):
                self.added.append((volume_id, root))
                return 1

            def remove_volume(self, volume_id):
                self.removed.append(volume_id)

            def read(self, *, timeout=0):
                return []

        self.scheduler.volumes.discovery = Discovery(self.files)
        watcher = Watcher()
        service = BackgroundIndexService(
            self.scheduler,
            watcher,
            mount_poll_seconds=1,
        )
        service.start()
        self.assertGreater(self.scheduler.status().queued, 0)
        VolumeRegistry(self.storage_db).set_permission("test-volume", PermissionLevel.NONE)
        service.tick(now=1)
        self.assertEqual(watcher.removed, ["test-volume"])

    def test_startup_rescans_existing_volume_and_marks_coverage(self) -> None:
        class Watcher:
            def add_tree(self, _volume_id, _root):
                return 1

            def remove_volume(self, _volume_id):
                return None

            def read(self, *, timeout=0):
                return []

        (self.files / "existing.txt").write_text("existing", encoding="utf-8")
        service = BackgroundIndexService(self.scheduler, Watcher())
        service.start()
        self.assertFalse(self.scheduler.status().coverage_complete)
        for cycle in range(1, 100):
            status = self.scheduler.process_once(now=cycle)
            if status.coverage_complete:
                break
        self.assertTrue(status.coverage_complete)
        self.assertEqual(status.covered_volume_ids, ("test-volume",))

    def test_incomplete_crawl_resumes_from_durable_checkpoint_after_restart(self) -> None:
        for index in range(20):
            (self.files / f"resume-{index:02}.txt").write_text(
                f"restart checkpoint {index}", encoding="utf-8"
            )
        self.scheduler.request_rescan("test-volume")
        first = self.scheduler.process_once(now=1)
        self.assertEqual(first.active_rescans, 1)
        scan_id = self.scheduler._crawls["test-volume"].scan_id
        current = self.scheduler._crawls["test-volume"].current
        if current is not None:
            current.close()

        resumed = IndexScheduler(
            storage_database=self.storage_db,
            index_database=self.index_db,
            queue=CoalescingEventQueue(debounce_seconds=0),
            budget=ResourceBudget(max_batch=3, load_probe=lambda: 0),
        )
        self.assertEqual(resumed._crawls["test-volume"].scan_id, scan_id)
        resumed.resume_or_request_rescan("test-volume")
        for cycle in range(2, 100):
            status = resumed.process_once(now=cycle)
            if status.coverage_complete:
                break

        self.assertTrue(status.coverage_complete)
        self.assertEqual(len(resumed.indexer.search("checkpoint", limit=50)), 20)


class QueueTests(unittest.TestCase):
    def test_coalesces_bursts_and_collapses_overflow_to_rescan(self) -> None:
        queue = CoalescingEventQueue(max_events=2, debounce_seconds=0)
        queue.put(FileEvent("v", "/a", EventKind.CREATED, 1))
        queue.put(FileEvent("v", "/a", EventKind.MODIFIED, 2))
        self.assertEqual(queue.pop_ready(now=3, limit=1)[0].kind, EventKind.CREATED)

        queue.put(FileEvent("v", "/a", EventKind.MODIFIED, 3))
        queue.put(FileEvent("v", "/b", EventKind.MODIFIED, 3))
        queue.put(FileEvent("v", "/c", EventKind.MODIFIED, 3))
        events = queue.pop_ready(now=4, limit=10)
        self.assertEqual(
            [(event.volume_id, event.kind, event.path) for event in events],
            [("*", EventKind.RESCAN, "")],
        )


@unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux inotify")
class LinuxInotifyIntegrationTests(unittest.TestCase):
    def test_observes_created_file_on_ubuntu_runner(self) -> None:
        root = PROJECT_ROOT / "tmp" / "inotify-tests" / str(uuid4())
        root.mkdir(parents=True)
        try:
            with LinuxInotifyWatcher(max_watches=32) as watcher:
                watcher.add_tree("volume", root)
                created = root / "event.txt"
                created.write_text("event", encoding="utf-8")
                events = []
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and not events:
                    events = watcher.read(timeout=0.2)
                self.assertTrue(any(event.path == str(created) for event in events))
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
