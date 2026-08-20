import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from ai_native_intents import TaskContextStore


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class PersistentTaskContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / "tmp" / "task-context-tests" / str(uuid4())
        self.database = self.root / "memory.sqlite3"

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_active_results_and_destination_survive_restart(self) -> None:
        original = TaskContextStore(locale="ru", database=self.database)
        original.set_active_results("collection-42")
        original.set_last_destination("/home/user/Desktop/math")

        restored = TaskContextStore(locale="ru", database=self.database).snapshot()

        self.assertEqual(restored.active_collection_id, "collection-42")
        self.assertEqual(restored.last_destination, "/home/user/Desktop/math")
        self.assertEqual(restored.locale, "ru")

    def test_clear_is_durable_and_locale_can_change(self) -> None:
        store = TaskContextStore(locale="ru", database=self.database)
        store.set_active_results("collection-1")
        store.clear()

        restored = TaskContextStore(locale="en", database=self.database).snapshot()

        self.assertIsNone(restored.active_collection_id)
        self.assertIsNone(restored.last_destination)
        self.assertEqual(restored.locale, "en")


if __name__ == "__main__":
    unittest.main()
