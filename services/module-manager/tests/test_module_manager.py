import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
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
        self.security_scan_root = root / "scan-root"
        self.security_scan_root.mkdir()
        self.manager = ModuleProcessManager(
            self.registry,
            idle_seconds=0,
            runtime_directory=self.root / "runtime",
            security_scan_roots={"test-files": self.security_scan_root},
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
        with patch.dict(
            "os.environ",
            {
                "AI_NATIVE_SECURITY_CLAMD_SOCKET": "/untrusted/inherited.sock",
                "AI_NATIVE_SECURITY_CLAMD_PEER_UIDS": "[999]",
            },
        ):
            provider = self.manager.start_for_capability("security.module.status")
            result = self.manager.invoke(provider, "status", timeout=5)

        self.assertEqual(provider, "security.center")
        self.assertEqual(
            result,
            {
                "schema_version": 1,
                "module_id": "security.center",
                "module_version": "0.6.0",
                "state": "ready",
                "lifecycle": "on-demand",
                "capabilities": [
                    "security.module.status",
                    "security.files.scan",
                    "security.scan.run",
                    "security.findings.list",
                    "security.posture.scan",
                    "security.quarantine.prepare",
                    "security.quarantine.commit",
                    "security.quarantine.restore",
                ],
            },
        )
        self.assertLessEqual(len(json.dumps(result).encode("utf-8")), 512)
        self.assertIn("security.center", self.manager.running_modules())
        self.assertEqual(
            self.manager.health_details("security.center"),
            {"status": "ready"},
        )

    def test_validates_security_clamd_configuration(self) -> None:
        valid_socket = Path(self.root.anchor) / "run" / "clamd.sock"
        valid = ModuleProcessManager(
            self.registry,
            runtime_directory=self.root / "valid-runtime",
            security_clamd_socket=valid_socket,
            security_clamd_peer_uids=[1000, 0, 1000],
        )
        self.addCleanup(valid.stop_all)
        self.assertEqual(valid.security_clamd_socket, valid_socket)
        self.assertEqual(valid.security_clamd_peer_uids, (0, 1000))

        posix_socket = Path("/run/clamd.sock")
        posix = ModuleProcessManager(
            self.registry,
            runtime_directory=self.root / "posix-runtime",
            security_clamd_socket=posix_socket,
            security_clamd_peer_uids=[0],
        )
        self.addCleanup(posix.stop_all)
        self.assertEqual(posix.security_clamd_socket, posix_socket)

        invalid_cases = (
            (
                {
                    "security_clamd_socket": "/run/clamd.sock",
                    "security_clamd_peer_uids": [0],
                },
                TypeError,
            ),
            (
                {
                    "security_clamd_socket": Path("relative/clamd.sock"),
                    "security_clamd_peer_uids": [0],
                },
                ValueError,
            ),
            (
                {
                    "security_clamd_socket": Path("C:/" + "x" * 257),
                    "security_clamd_peer_uids": [0],
                },
                ValueError,
            ),
            (
                {
                    "security_clamd_socket": Path("C:/clamd\x00.sock"),
                    "security_clamd_peer_uids": [0],
                },
                ValueError,
            ),
            (
                {
                    "security_clamd_socket": valid_socket,
                    "security_clamd_peer_uids": [],
                },
                ValueError,
            ),
            (
                {
                    "security_clamd_socket": valid_socket,
                    "security_clamd_peer_uids": [True],
                },
                TypeError,
            ),
            (
                {
                    "security_clamd_socket": valid_socket,
                    "security_clamd_peer_uids": ["0"],
                },
                TypeError,
            ),
            (
                {
                    "security_clamd_socket": valid_socket,
                    "security_clamd_peer_uids": [-1],
                },
                ValueError,
            ),
            (
                {
                    "security_clamd_socket": valid_socket,
                    "security_clamd_peer_uids": [2**31],
                },
                ValueError,
            ),
            (
                {
                    "security_clamd_socket": valid_socket,
                    "security_clamd_peer_uids": list(range(17)),
                },
                ValueError,
            ),
        )
        for arguments, error_type in invalid_cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(error_type):
                    ModuleProcessManager(
                        self.registry,
                        runtime_directory=self.root / "invalid-runtime",
                        **arguments,
                    )

    def test_rejects_finding_database_inside_security_scan_root(self) -> None:
        runtime = self.root / "overlapping-runtime"
        runtime.mkdir()

        with self.assertRaisesRegex(ValueError, "outside scan roots"):
            ModuleProcessManager(
                self.registry,
                runtime_directory=runtime,
                security_scan_roots={"runtime": self.root},
            )

    def test_builds_clean_clamd_environment_only_for_security_center(self) -> None:
        socket_path = Path(self.root.anchor) / "run" / "clamd.sock"
        manager = ModuleProcessManager(
            self.registry,
            runtime_directory=self.root / "clamd-runtime",
            security_clamd_socket=socket_path,
            security_clamd_peer_uids={1001, 0},
        )
        self.addCleanup(manager.stop_all)
        inherited = {
            "AI_NATIVE_SECURITY_CLAMD_SOCKET": "/attacker/socket",
            "AI_NATIVE_SECURITY_CLAMD_PEER_UIDS": "[999]",
            "PRESERVED": "yes",
        }

        security_env = dict(inherited)
        manager._configure_security_clamd_environment(security_env, "security.center")
        self.assertEqual(
            security_env["AI_NATIVE_SECURITY_CLAMD_SOCKET"],
            str(socket_path),
        )
        self.assertEqual(
            security_env["AI_NATIVE_SECURITY_CLAMD_PEER_UIDS"],
            "[0,1001]",
        )
        self.assertEqual(security_env["PRESERVED"], "yes")

        ordinary_env = dict(inherited)
        manager._configure_security_clamd_environment(ordinary_env, "system.monitor")
        self.assertNotIn("AI_NATIVE_SECURITY_CLAMD_SOCKET", ordinary_env)
        self.assertNotIn("AI_NATIVE_SECURITY_CLAMD_PEER_UIDS", ordinary_env)

    def test_removes_inherited_clamd_environment_when_adapter_is_disabled(self) -> None:
        env = {
            "AI_NATIVE_SECURITY_CLAMD_SOCKET": "/attacker/socket",
            "AI_NATIVE_SECURITY_CLAMD_PEER_UIDS": "[999]",
        }

        self.manager._configure_security_clamd_environment(env, "security.center")

        self.assertEqual(env, {})

    def test_starts_security_scan_provider_in_isolated_on_demand_worker(self) -> None:
        sample = self.security_scan_root / "sample.txt"
        sample.write_bytes(b"ordinary sample")
        provider = self.manager.start_for_capability("security.files.scan")
        result = self.manager.invoke(
            provider,
            "scan",
            {"resource_id": "test-files", "relative_path": "sample.txt"},
            timeout=30,
        )

        self.assertEqual(provider, "security.center")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["verdict"], "no_threat_detected")
        self.assertEqual(result["size_bytes"], len(b"ordinary sample"))
        self.assertNotIn(str(self.security_scan_root), json.dumps(result))
        self.assertIn("security.center", self.manager.running_modules())
        self.assertTrue(self.manager.health("security.center"))

    def test_runs_security_profile_and_reads_durable_finding_store(self) -> None:
        (self.security_scan_root / "a.txt").write_bytes(b"ordinary sample")
        nested = self.security_scan_root / "nested"
        nested.mkdir()
        (nested / "b.txt").write_bytes(b"another sample")
        provider = self.manager.start_for_capability("security.scan.run")

        result = self.manager.invoke(
            provider,
            "scan_profile",
            {"resource_id": "test-files", "mode": "quick"},
            timeout=30,
        )
        findings = self.manager.invoke(provider, "findings_list", {}, timeout=5)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["verdict"], "no_threat_detected")
        self.assertEqual(result["scanned_files"], 2)
        self.assertEqual(findings["findings"], [])
        self.assertTrue(self.manager.security_findings_database.is_file())
        encoded = json.dumps({"scan": result, "findings": findings})
        self.assertNotIn(str(self.security_scan_root), encoded)

    def test_security_posture_is_bounded_in_isolated_worker(self) -> None:
        provider = self.manager.start_for_capability("security.posture.scan")

        result = self.manager.invoke(provider, "posture_scan", {}, timeout=30)

        self.assertEqual(provider, "security.center")
        self.assertEqual(result["schema_version"], 1)
        self.assertIn(result["status"], {"completed", "partial", "unsupported"})
        self.assertLessEqual(len(json.dumps(result).encode("utf-8")), 16 * 1024)

    def test_configured_unavailable_clamd_is_partial_through_isolated_worker(self) -> None:
        sample = self.security_scan_root / "clamd-sample.txt"
        sample.write_bytes(b"ordinary sample")
        manager = ModuleProcessManager(
            self.registry,
            runtime_directory=self.root / "clamd-worker-runtime",
            security_scan_roots={"test-files": self.security_scan_root},
            security_clamd_socket=self.root / "missing-clamd.sock",
            security_clamd_peer_uids={0},
        )
        self.addCleanup(manager.stop_all)

        provider = manager.start_for_capability("security.files.scan")
        result = manager.invoke(
            provider,
            "scan",
            {"resource_id": "test-files", "relative_path": "clamd-sample.txt"},
            timeout=30,
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["verdict"], "unknown")
        self.assertEqual(result["error_code"], "detector_unavailable")
        self.assertEqual(result["detectors"][1]["detector"], "clamd")
        self.assertEqual(result["detectors"][1]["state"], "unavailable")

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
