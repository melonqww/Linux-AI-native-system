"""Guards the lab boundary against a second, manifest-parsing registry."""

import shutil
from pathlib import Path
from uuid import uuid4

from ai_native_capabilities import CapabilityRegistry
from ai_native_permissions import PermissionGateway, builtin_policies
from ai_scenario_lab.environment import _registry_descriptors


PROJECT_ROOT = Path(__file__).resolve().parents[3]
LAB_ROOT = Path(__file__).resolve().parents[1]


def test_lab_routes_only_enabled_executor_capabilities_from_production_registry():
    root = LAB_ROOT / ".runtime" / "registry-tests" / uuid4().hex
    root.mkdir(parents=True)
    try:
        registry = CapabilityRegistry(root / "capabilities.sqlite3")
        report = registry.sync([PROJECT_ROOT / "services", PROJECT_ROOT / "modules"])
        assert report.issues == ()

        available = (
            "documents.query.search",
            "storage.materialize.plan-copy",
        )
        policies = PermissionGateway(builtin_policies()).policy
        descriptors = _registry_descriptors(registry, available, policies)
        assert {
            (descriptor.capability_id, descriptor.operation)
            for descriptor in descriptors
        } == {
            ("documents.query.search", "search_documents"),
            ("storage.materialize.plan-copy", "copy_results"),
        }

        registry.set_enabled("documents.query", False)
        descriptors = _registry_descriptors(registry, available, policies)
        assert [descriptor.capability_id for descriptor in descriptors] == [
            "storage.materialize.plan-copy"
        ]
    finally:
        shutil.rmtree(root, ignore_errors=True)
