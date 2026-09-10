import json
import shutil
import sys
import unittest
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "services" / "capability-registry" / "src"))

from ai_native_capabilities import CapabilityRegistry
from ai_native_module_manager import ModuleProcessError, ModuleProcessManager


class ModuleManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        root = PROJECT_ROOT / "tmp" / "module-manager-tests" / str(uuid4())
        root.mkdir(parents=True)
        self.root = root
        self.registry = CapabilityRegistry(root / "registry.sqlite3")
        self.registry.sync([PROJECT_ROOT / "services", PROJECT_ROOT / "modules"])
        self.manager = ModuleProcessManager(
            self.registry,
            idle_seconds=0,
            runtime_directory=self.root / "runtime",
        )

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

    def test_starts_background_watcher_with_required_dependencies(self) -> None:
        provider = self.manager.start_for_capability("storage.watch.events")
        self.assertEqual(provider, "storage.watch")
        self.assertEqual(
            self.manager.running_modules(),
            ("documents.index", "storage.catalog", "storage.watch"),
        )
        self.assertTrue(self.manager.health_details("storage.watch")["thread_alive"])
        # Background modules are not removed by the on-demand idle reaper.
        self.assertNotIn("storage.watch", self.manager.reap_idle(now=float("inf")))

    def test_invokes_system_monitor_in_isolated_worker(self) -> None:
        provider = self.manager.start_for_capability("system.monitor.snapshot")
        result = self.manager.invoke(
            provider,
            "snapshot",
            {"process_limit": 5, "process_sort": "memory", "process_order": "desc"},
        )

        self.assertEqual(provider, "system.monitor")
        self.assertEqual(result["schema_version"], 1)
        self.assertIn("cpu", result)
        self.assertIn("physical_cores", result["cpu"])
        self.assertIn("logical_cpus", result["cpu"])
        self.assertIn("memory", result)
        self.assertIn("installed_modules", result["memory"])
        self.assertIn("channel_mode", result["memory"])
        self.assertLessEqual(len(result["processes"]), 5)
        self.assertLessEqual(len(json.dumps(result).encode("utf-8")), 64 * 1024)
        self.assertIn("system.monitor", self.manager.running_modules())

    def test_invokes_system_updates_in_isolated_on_demand_worker(self) -> None:
        provider = self.manager.start_for_capability("system.updates.check")
        result = self.manager.invoke(provider, "check", timeout=30)

        self.assertEqual(provider, "system.updates")
        self.assertEqual(result["schema_version"], 1)
        self.assertIn(result["state"], {"updates_available", "up_to_date", "unavailable"})
        self.assertLessEqual(len(json.dumps(result).encode("utf-8")), 64 * 1024)

    def test_invokes_security_foundation_in_isolated_on_demand_worker(self) -> None:
        provider = self.manager.start_for_capability("security.module.status")
        result = self.manager.invoke(provider, "status", timeout=5)

        self.assertEqual(provider, "security.center")
        self.assertEqual(
            result,
            {
                "schema_version": 1,
                "module_id": "security.center",
                "module_version": "0.1.0",
                "state": "ready",
                "lifecycle": "on-demand",
                "capabilities": ["security.module.status"],
            },
        )
        self.assertLessEqual(len(json.dumps(result).encode("utf-8")), 512)
        self.assertIn("security.center", self.manager.running_modules())
        self.assertEqual(
            self.manager.health_details("security.center"),
            {"status": "ready"},
        )

    def test_reads_software_snapshot_from_isolated_background_worker(self) -> None:
        provider = self.manager.start_for_capability("software.catalog.read")
        result = self.manager.invoke(provider, "snapshot", timeout=15)

        self.assertEqual(provider, "software.manager")
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["provider"], "snap")
        self.assertEqual(len(result["catalog"]), 20)
        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["backups"], [])
        self.assertIn("software.manager", self.manager.running_modules())
        self.assertNotIn("software.manager", self.manager.reap_idle(now=float("inf")))
        self.assertFalse(self.manager.health_details(provider)["recovery_active"])
        activated = self.manager.invoke(provider, "activate", timeout=15)
        self.assertTrue(activated["recovery_active"])
        self.assertTrue(self.manager.health_details(provider)["recovery_active"])

    def test_invokes_ollama_provider_status_without_starting_download(self) -> None:
        provider = self.manager.start_for_capability("provider.ollama.status")
        result = self.manager.invoke(provider, "status", timeout=15)

        self.assertEqual(provider, "provider.ollama")
        self.assertEqual(result["provider_id"], "ollama")
        self.assertIn(
            result["state"],
            {
                "ready",
                "consent_required",
                "deferred",
                "declined",
                "unsupported",
                "error",
            },
        )

    def test_rejects_untrusted_worker_operations_before_sending(self) -> None:
        self.manager.start_module("system.monitor")
        for operation in ("", "../snapshot", "Snapshot", "x" * 65):
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    self.manager.invoke("system.monitor", operation)
        with self.assertRaises(ValueError):
            self.manager.invoke("system.monitor", "snapshot", [])
        for timeout in (0, 61, True, "30"):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    self.manager.invoke("system.monitor", "snapshot", timeout=timeout)

    def test_worker_redacts_module_failure_and_remains_healthy(self) -> None:
        self.manager.start_module("system.monitor")

        with self.assertRaisesRegex(ModuleProcessError, "invocation_failed"):
            self.manager.invoke("system.monitor", "unknown")

        self.assertTrue(self.manager.health("system.monitor"))

    def test_rejects_oversized_worker_request(self) -> None:
        self.manager.start_module("system.monitor")

        with self.assertRaisesRegex(ModuleProcessError, "too large"):
            self.manager.invoke("system.monitor", "snapshot", {"padding": "x" * 70_000})

        self.assertTrue(self.manager.health("system.monitor"))


if __name__ == "__main__":
    unittest.main()
