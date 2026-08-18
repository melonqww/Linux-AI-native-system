"""Manifest validation and dependency-aware Registry tests."""

import json
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from ai_native_capabilities import CapabilityRegistry, ManifestValidationError, ModuleState
from ai_native_capabilities.manifest import load_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def manifest_payload(
    module_id: str,
    *,
    dependencies: list[str] | None = None,
    default_enabled: bool = True,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "module_id": module_id,
        "display_name": module_id,
        "description": f"Test module {module_id}",
        "module_version": "1.0.0",
        "core_api": "1",
        "capabilities": [f"{module_id}.run"],
        "dependencies": dependencies or [],
        "optional_dependencies": [],
        "requested_permissions": [],
        "lifecycle": "on-demand",
        "resource_class": "tiny",
        "default_enabled": default_enabled,
        "entrypoint": {
            "kind": "python-package",
            "module": "test_module",
            "python_path": "src",
        },
    }


class RegistryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary_root = PROJECT_ROOT / "tmp" / "capability-registry-tests"
        self.base = temporary_root / str(uuid4())
        self.base.mkdir(parents=True)
        self.database = self.base / "registry.sqlite3"
        self.registry = CapabilityRegistry(self.database)

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def write_manifest(self, payload: dict[str, object], directory_name: str | None = None) -> Path:
        module_id = str(payload["module_id"])
        module_root = self.base / (directory_name or module_id.replace(".", "-"))
        (module_root / "src").mkdir(parents=True)
        path = module_root / "module.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path


class ManifestValidationTests(RegistryTestCase):
    def test_rejects_unknown_fields(self) -> None:
        payload = manifest_payload("test.module")
        payload["unexpected"] = "unsafe"
        path = self.write_manifest(payload)

        with self.assertRaises(ManifestValidationError):
            load_manifest(path)

    def test_rejects_entrypoint_path_escape(self) -> None:
        payload = manifest_payload("test.module")
        entrypoint = payload["entrypoint"]
        assert isinstance(entrypoint, dict)
        entrypoint["python_path"] = "../outside"
        path = self.write_manifest(payload)

        with self.assertRaises(ManifestValidationError):
            load_manifest(path)

    def test_project_first_party_manifests_are_valid(self) -> None:
        storage = load_manifest(PROJECT_ROOT / "services" / "storage-catalog" / "module.json")
        index = load_manifest(PROJECT_ROOT / "services" / "indexer" / "module.json")

        self.assertEqual(storage.module_id, "storage.catalog")
        self.assertEqual(index.dependencies, ("storage.catalog",))


class CapabilityRegistryTests(RegistryTestCase):
    def test_registers_first_party_modules_and_publishes_capabilities(self) -> None:
        report = self.registry.sync([PROJECT_ROOT / "services"])

        self.assertEqual(report.scanned, 2)
        self.assertEqual(report.registered, 2)
        self.assertEqual(report.issues, ())
        self.assertEqual(
            [module.state for module in self.registry.list_modules()],
            [ModuleState.ENABLED, ModuleState.ENABLED],
        )
        providers = self.registry.providers("documents.text.search")
        self.assertEqual([provider.module_id for provider in providers], ["documents.index"])

    def test_registers_complete_first_party_module_set(self) -> None:
        report = self.registry.sync([PROJECT_ROOT / "services", PROJECT_ROOT / "modules"])

        self.assertEqual(report.scanned, 5)
        self.assertEqual(report.issues, ())
        self.assertEqual(
            [module.manifest.module_id for module in self.registry.list_modules()],
            [
                "browser.navigation",
                "desktop.applications",
                "documents.index",
                "documents.pdf",
                "storage.catalog",
            ],
        )
        self.assertIn("documents.pdf.extract", self.registry.available_capabilities())
        self.assertIn("browser.search.plan", self.registry.available_capabilities())

    def test_disabling_dependency_makes_dependent_module_unavailable(self) -> None:
        self.registry.sync([PROJECT_ROOT / "services"])

        storage = self.registry.set_enabled("storage.catalog", False)
        documents = self.registry.get_module("documents.index")

        self.assertEqual(storage.state, ModuleState.DISABLED)
        self.assertEqual(documents.state, ModuleState.UNAVAILABLE)
        self.assertEqual(documents.state_reason, "dependency_not_enabled:storage.catalog")
        self.assertNotIn("documents.text.search", self.registry.available_capabilities())

        self.registry.set_enabled("storage.catalog", True)
        self.assertEqual(self.registry.get_module("documents.index").state, ModuleState.ENABLED)

    def test_user_choice_survives_manifest_resync(self) -> None:
        self.registry.sync([PROJECT_ROOT / "services"])
        self.registry.set_enabled("documents.index", False)

        self.registry.sync([PROJECT_ROOT / "services"])

        documents = self.registry.get_module("documents.index")
        self.assertFalse(documents.desired_enabled)
        self.assertEqual(documents.state, ModuleState.DISABLED)

    def test_quarantine_removes_module_and_dependents_from_available_capabilities(self) -> None:
        self.registry.sync([PROJECT_ROOT / "services"])

        self.registry.quarantine("storage.catalog", "repeated health-check failure")

        self.assertEqual(self.registry.get_module("storage.catalog").state, ModuleState.QUARANTINED)
        self.assertEqual(self.registry.get_module("documents.index").state, ModuleState.UNAVAILABLE)
        self.assertEqual(self.registry.available_capabilities(), [])

        self.registry.clear_quarantine("storage.catalog")
        self.assertEqual(self.registry.get_module("storage.catalog").state, ModuleState.ENABLED)

    def test_missing_dependency_is_explained(self) -> None:
        path = self.write_manifest(
            manifest_payload("test.consumer", dependencies=["test.missing"])
        )

        self.registry.sync([path])

        module = self.registry.get_module("test.consumer")
        self.assertEqual(module.state, ModuleState.UNAVAILABLE)
        self.assertEqual(module.state_reason, "missing_dependency:test.missing")

    def test_dependency_cycle_disables_every_cycle_member(self) -> None:
        first = self.write_manifest(
            manifest_payload("cycle.first", dependencies=["cycle.second"])
        )
        second = self.write_manifest(
            manifest_payload("cycle.second", dependencies=["cycle.first"])
        )

        self.registry.sync([first, second])

        self.assertEqual(self.registry.get_module("cycle.first").state_reason, "dependency_cycle")
        self.assertEqual(self.registry.get_module("cycle.second").state_reason, "dependency_cycle")

    def test_default_disabled_module_stays_installed_until_user_enables_it(self) -> None:
        path = self.write_manifest(
            manifest_payload("test.optional", default_enabled=False)
        )
        self.registry.sync([path])

        self.assertEqual(self.registry.get_module("test.optional").state, ModuleState.INSTALLED)

        self.registry.set_enabled("test.optional", True)
        self.assertEqual(self.registry.get_module("test.optional").state, ModuleState.ENABLED)

    def test_invalid_manifest_update_removes_old_capability(self) -> None:
        path = self.write_manifest(manifest_payload("test.mutable"))
        self.registry.sync([path])
        payload = manifest_payload("test.mutable")
        payload["unexpected"] = True
        path.write_text(json.dumps(payload), encoding="utf-8")

        report = self.registry.sync([path])

        self.assertEqual(len(report.issues), 1)
        self.assertEqual(self.registry.get_module("test.mutable").state, ModuleState.UNAVAILABLE)
        self.assertEqual(self.registry.available_capabilities(), [])


if __name__ == "__main__":
    unittest.main()
