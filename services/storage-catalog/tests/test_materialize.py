import shutil
import errno
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "services" / "storage-catalog" / "src"))

from ai_native_storage import ApprovalAuthority, CollectionItem, MaterializeService


class MaterializeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / "tmp" / "materialize-tests" / str(uuid4())
        self.root.mkdir(parents=True)
        self.database = self.root / "catalog.sqlite3"
        self.approval = ApprovalAuthority()
        self.service = MaterializeService(self.database, self.approval)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _plan(self):
        source = self.root / "algebra.pdf"
        source.write_bytes(b"pdf-content")
        collection = self.service.collections.create_snapshot(
            "Math",
            [CollectionItem("volume", str(source), "stable", source.name)],
        )
        # Snapshot resolution normally uses the catalog.  Patch the resolved item
        # here so this unit test is focused exclusively on copy semantics.
        self.service.collections.resolve = lambda _collection_id: [
            CollectionItem("volume", str(source), "stable", source.name, available=True)
        ]
        return source, self.service.create_copy_plan(collection.collection_id, self.root / "result")

    def test_requires_one_time_explicit_approval(self) -> None:
        _source, plan = self._plan()
        with self.assertRaises(PermissionError):
            self.approval.approve(plan.plan_id, user_confirmed=False)
        grant = self.approval.approve(plan.plan_id, user_confirmed=True)
        copied = self.service.execute(plan.plan_id, grant)
        self.assertEqual(Path(copied[0]).read_bytes(), b"pdf-content")
        with self.assertRaises((KeyError, PermissionError)):
            self.service.execute(plan.plan_id, grant)

    def test_rejects_source_changed_after_approval(self) -> None:
        source, plan = self._plan()
        grant = self.approval.approve(plan.plan_id, user_confirmed=True)
        source.write_bytes(b"changed-after-user-confirmed")
        with self.assertRaises(RuntimeError):
            self.service.execute(plan.plan_id, grant)
        self.assertFalse((self.root / "result").exists())

    def test_detects_content_change_even_when_size_and_mtime_are_restored(self) -> None:
        source, plan = self._plan()
        grant = self.approval.approve(plan.plan_id, user_confirmed=True)
        original = source.stat()
        source.write_bytes(b"bad-content")
        source.touch()
        import os
        os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))

        with self.assertRaisesRegex(RuntimeError, "source changed"):
            self.service.execute(plan.plan_id, grant)
        self.assertFalse((self.root / "result").exists())

    def test_discard_revokes_plan(self) -> None:
        _source, plan = self._plan()
        grant = self.approval.approve(plan.plan_id, user_confirmed=True)
        self.service.discard(plan.plan_id)
        with self.assertRaises(KeyError):
            self.service.execute(plan.plan_id, grant)

    def test_partial_copy_is_rolled_back_without_deleting_external_file(self) -> None:
        first = self.root / "first.pdf"
        second = self.root / "second.pdf"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        collection = self.service.collections.create_snapshot("Two", [])
        self.service.collections.resolve = lambda _collection_id: [
            CollectionItem("volume", str(first), "first", first.name, available=True),
            CollectionItem("volume", str(second), "second", second.name, available=True),
        ]
        destination = self.root / "result"
        plan = self.service.create_copy_plan(collection.collection_id, destination)
        destination.mkdir()
        external = destination / "second.pdf"
        external.write_bytes(b"external")
        grant = self.approval.approve(plan.plan_id, user_confirmed=True)

        with self.assertRaises(FileExistsError):
            self.service.execute(plan.plan_id, grant)

        self.assertFalse((destination / "first.pdf").exists())
        self.assertEqual(external.read_bytes(), b"external")

    def test_filesystem_without_hardlinks_uses_no_clobber_streaming_fallback(self) -> None:
        _source, plan = self._plan()
        grant = self.approval.approve(plan.plan_id, user_confirmed=True)

        with patch(
            "ai_native_storage.materialize.os.link",
            side_effect=PermissionError(errno.EPERM, "unsupported"),
        ):
            copied = self.service.execute(plan.plan_id, grant)

        self.assertEqual(Path(copied[0]).read_bytes(), b"pdf-content")


if __name__ == "__main__":
    unittest.main()
