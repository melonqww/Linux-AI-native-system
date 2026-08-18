import shutil
import sys
import unittest
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "services" / "capability-registry" / "src"))

from ai_native_capabilities import CapabilityRegistry
from ai_native_module_manager import ModuleProcessManager


class ModuleManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        root = PROJECT_ROOT / "tmp" / "module-manager-tests" / str(uuid4())
        root.mkdir(parents=True)
        self.root = root
        self.registry = CapabilityRegistry(root / "registry.sqlite3")
        self.registry.sync([PROJECT_ROOT / "services", PROJECT_ROOT / "modules"])
        self.manager = ModuleProcessManager(self.registry, idle_seconds=0)

    def tearDown(self) -> None:
        self.manager.stop_all()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_starts_provider_and_dependency_in_isolated_processes(self) -> None:
        provider = self.manager.start_for_capability("documents.text.search")

        self.assertEqual(provider, "documents.index")
        self.assertEqual(
            self.manager.running_modules(),
            ("documents.index", "storage.catalog"),
        )
        self.assertTrue(self.manager.health("documents.index"))

    def test_reaps_idle_on_demand_modules(self) -> None:
        self.manager.start_module("storage.catalog")
        stopped = self.manager.reap_idle(now=float("inf"))

        self.assertEqual(stopped, ["storage.catalog"])
        self.assertEqual(self.manager.running_modules(), ())

    def test_starts_pdf_module_with_its_dependencies(self) -> None:
        provider = self.manager.start_for_capability("documents.pdf.extract")
        self.assertEqual(provider, "documents.pdf")
        self.assertEqual(
            self.manager.running_modules(),
            ("documents.index", "documents.pdf", "storage.catalog"),
        )


if __name__ == "__main__":
    unittest.main()
