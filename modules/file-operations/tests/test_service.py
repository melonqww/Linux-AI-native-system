import errno
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from ai_native_file_operations import FileOperationError, FileOperationsService


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class FileOperationsServiceTests(unittest.TestCase):
    def setUp(self):
        self.root = (PROJECT_ROOT / "tmp" / "file-operations" / str(uuid4())).resolve()
        self.assertTrue(self.root.is_relative_to((PROJECT_ROOT / "tmp").resolve()))
        self.root.mkdir(parents=True)
        self.desktop = self.root / "Desktop"
        self.documents = self.root / "Documents"
        self.downloads = self.root / "Downloads"
        for path in (self.desktop, self.documents, self.downloads):
            path.mkdir()
        self.selections: dict[str, tuple[Path, ...]] = {}
        self.clock_value = 100.0
        self.service = FileOperationsService(
            destination_roots={
                "desktop": self.desktop,
                "documents": self.documents,
                "downloads": self.downloads,
            },
            trash_root=self.root / "Trash",
            selection_resolver=lambda key: self.selections[key],
            plan_ttl_seconds=60,
            max_items=3,
            clock=lambda: self.clock_value,
        )

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _file(self, parent: Path, name: str, content: str = "data") -> Path:
        target = parent / name
        target.write_text(content, encoding="utf-8")
        return target

    def _error(self, code: str, callback, *args, **kwargs):
        with self.assertRaises(FileOperationError) as captured:
            callback(*args, **kwargs)
        self.assertEqual(captured.exception.code, code)

    def test_inspect_returns_bounded_relative_metadata_without_absolute_paths(self):
        source = self._file(self.documents, "report.txt", "hello")
        self.selections["selection"] = (source,)

        result = self.service.inspect("selection")

        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["item_count"], 1)
        self.assertEqual(
            result["items"][0],
            {
                "name": "report.txt",
                "kind": "file",
                "size_bytes": 5,
                "modified_ns": source.stat().st_mtime_ns,
                "location": "documents",
                "relative_path": "report.txt",
            },
        )
        self.assertNotIn(str(self.root), str(result))

    def test_inspect_skips_unpermitted_search_volume_without_exposing_it(self):
        permitted = self._file(self.documents, "report.txt", "hello")
        archive = self.root / "Archive"
        archive.mkdir()
        other = self._file(archive, "private.txt", "secret")
        self.selections["selection"] = (permitted, other)

        result = self.service.inspect("selection")

        self.assertEqual(result["item_count"], 1)
        self.assertEqual(result["unavailable_count"], 1)
        self.assertEqual(result["items"][0]["name"], "report.txt")
        self.assertNotIn("private.txt", repr(result))
        self.selections["outside"] = (other,)
        self._error("selection_outside_allowed_roots", self.service.inspect, "outside")

    def test_create_directory_has_no_effect_before_commit_and_cannot_replay(self):
        prepared = self.service.prepare_create_directory("desktop", "Projects")
        target = self.desktop / "Projects"
        self.assertFalse(target.exists())

        committed = self.service.commit(prepared["plan_id"])

        self.assertTrue(target.is_dir())
        self.assertEqual(committed["created_count"], 1)
        self.assertEqual(committed["state"], "committed")
        self._error("plan_not_prepared", self.service.commit, prepared["plan_id"])

    def test_cancelled_plan_never_changes_the_filesystem(self):
        prepared = self.service.prepare_create_directory("documents", "Cancelled")

        cancelled = self.service.cancel(prepared["plan_id"])

        self.assertEqual(cancelled["state"], "cancelled")
        self.assertFalse((self.documents / "Cancelled").exists())
        self._error("plan_not_prepared", self.service.commit, prepared["plan_id"])

    def test_move_uses_trusted_selection_and_optional_child_directory(self):
        first = self._file(self.documents, "one.txt")
        second = self._file(self.documents, "two.txt")
        self.selections["found"] = (first, second)
        prepared = self.service.prepare_move(
            "found", "desktop", directory_name="Work"
        )
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())

        result = self.service.commit(prepared["plan_id"])

        self.assertEqual(result["moved_count"], 2)
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertTrue((self.desktop / "Work" / "one.txt").is_file())
        self.assertTrue((self.desktop / "Work" / "two.txt").is_file())

    def test_move_conflict_fails_without_changing_source_or_destination(self):
        source = self._file(self.documents, "same.txt", "source")
        existing = self._file(self.desktop, "same.txt", "destination")
        self.selections["found"] = (source,)
        prepared = self.service.prepare_move("found", "desktop")

        self._error("destination_exists", self.service.commit, prepared["plan_id"])

        self.assertEqual(source.read_text(encoding="utf-8"), "source")
        self.assertEqual(existing.read_text(encoding="utf-8"), "destination")

    def test_partial_multi_item_move_is_rolled_back(self):
        first = self._file(self.documents, "one.txt")
        second = self._file(self.documents, "two.txt")
        self.selections["found"] = (first, second)
        prepared = self.service.prepare_move("found", "desktop")
        real_replace = os.replace

        def fail_second_source(source, target):
            if Path(source) == second:
                raise OSError(errno.EACCES, "simulated failure")
            return real_replace(source, target)

        with patch(
            "ai_native_file_operations.service.os.replace",
            side_effect=fail_second_source,
        ):
            self._error("move_failed", self.service.commit, prepared["plan_id"])

        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())
        self.assertFalse((self.desktop / "one.txt").exists())
        self.assertFalse((self.desktop / "two.txt").exists())

    def test_rename_requires_one_item_and_can_choose_non_conflicting_name(self):
        first = self._file(self.documents, "first.txt")
        second = self._file(self.documents, "second.txt")
        self.selections["many"] = (first, second)
        self._error(
            "rename_requires_one_item",
            self.service.prepare_rename,
            "many",
            "renamed.txt",
        )

        self._file(self.documents, "renamed.txt", "existing")
        self.selections["one"] = (first,)
        prepared = self.service.prepare_rename(
            "one", "renamed.txt", conflict_policy="rename"
        )
        result = self.service.commit(prepared["plan_id"])

        self.assertEqual(result["new_name"], "renamed (1).txt")
        self.assertTrue((self.documents / "renamed (1).txt").is_file())

    def test_trash_is_recoverable_and_writes_freedesktop_metadata(self):
        source = self._file(self.downloads, "old.txt")
        self.selections["old"] = (source,)
        prepared = self.service.prepare_trash("old")

        result = self.service.commit(prepared["plan_id"])

        trashed = self.root / "Trash" / "files" / "old.txt"
        metadata = self.root / "Trash" / "info" / "old.txt.trashinfo"
        self.assertEqual(result["trashed_count"], 1)
        self.assertFalse(source.exists())
        self.assertTrue(trashed.is_file())
        self.assertIn("[Trash Info]", metadata.read_text(encoding="utf-8"))
        self.assertIn("DeletionDate=", metadata.read_text(encoding="utf-8"))

    def test_source_change_after_prepare_fails_closed(self):
        source = self._file(self.documents, "mutable.txt", "before")
        self.selections["one"] = (source,)
        prepared = self.service.prepare_move("one", "desktop")
        source.write_text("after and different", encoding="utf-8")

        self._error("selection_changed", self.service.commit, prepared["plan_id"])

        self.assertTrue(source.is_file())
        self.assertFalse((self.desktop / "mutable.txt").exists())

    def test_expired_plan_and_unsafe_names_fail_without_effects(self):
        prepared = self.service.prepare_create_directory("desktop", "Later")
        self.clock_value = 161.0
        self._error("plan_expired", self.service.commit, prepared["plan_id"])
        self.assertFalse((self.desktop / "Later").exists())

        for name in ("../escape", "nested/name", "nested\\name", " ", "."):
            self._error(
                "invalid_name",
                self.service.prepare_create_directory,
                "desktop",
                name,
            )
        self._error(
            "invalid_destination",
            self.service.prepare_create_directory,
            "context.last_destination",
            "Folder",
        )

    def test_outside_symlink_and_oversized_selections_are_rejected(self):
        outside = self._file(self.root, "outside.txt")
        self.selections["outside"] = (outside,)
        self._error("selection_outside_allowed_roots", self.service.inspect, "outside")

        items = tuple(self._file(self.documents, f"{index}.txt") for index in range(4))
        self.selections["too-many"] = items
        self._error("selection_size_out_of_bounds", self.service.inspect, "too-many")

        target = self._file(self.documents, "target.txt")
        link = self.documents / "link.txt"
        try:
            link.symlink_to(target)
        except (NotImplementedError, OSError):
            self.skipTest("symlinks are unavailable on this host")
        self.selections["link"] = (link,)
        self._error("unsafe_path_component", self.service.inspect, "link")

    def test_destination_roots_must_not_overlap(self):
        nested = self.desktop / "nested"
        nested.mkdir()
        with self.assertRaisesRegex(ValueError, "destination_roots_overlap"):
            FileOperationsService(
                destination_roots={
                    "desktop": self.desktop,
                    "documents": nested,
                    "downloads": self.downloads,
                },
                trash_root=self.root / "OtherTrash",
                selection_resolver=lambda key: (),
            )


if __name__ == "__main__":
    unittest.main()
