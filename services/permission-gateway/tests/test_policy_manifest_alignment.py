import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from ai_native_capabilities import CapabilityRegistry
from ai_native_permissions import PermissionGateway, builtin_policies


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class PolicyManifestAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.root = PROJECT_ROOT / "tmp" / "policy-manifest" / str(uuid4())
        self.root.mkdir(parents=True)
        self.registry = CapabilityRegistry(self.root / "registry.sqlite3")
        report = self.registry.sync(
            [PROJECT_ROOT / "services", PROJECT_ROOT / "modules"]
        )
        self.assertFalse(report.issues)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_every_provider_declares_scopes_required_by_core_policy(self):
        gateway = PermissionGateway(builtin_policies())
        checked = 0
        for policy in builtin_policies():
            required = gateway.required_scopes(policy.capability_id)
            for provider in self.registry.providers(policy.capability_id):
                manifest = self.registry.get_module(provider.module_id).manifest
                self.assertTrue(
                    required.issubset(manifest.requested_permissions),
                    f"{provider.module_id} misses {sorted(required - set(manifest.requested_permissions))}",
                )
                checked += 1
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
