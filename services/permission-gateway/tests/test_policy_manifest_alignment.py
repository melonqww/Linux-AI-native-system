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
                contract = next(
                    item
                    for item in manifest.capabilities
                    if item.capability_id == policy.capability_id
                )
                self.assertTrue(
                    required.issubset(contract.requested_permissions),
                    f"{provider.module_id} capability misses "
                    f"{sorted(required - set(contract.requested_permissions))}",
                )
                self.assertEqual(
                    set(contract.input_schema["properties"]),
                    set(policy.allowed_arguments),
                    f"{provider.module_id} argument contract drift",
                )
                self.assertTrue(
                    set(policy.required_arguments)
                    <= set(contract.input_schema["required"]),
                    f"{provider.module_id} required argument contract drift",
                )
                checked += 1
        self.assertGreater(checked, 0)

    def test_security_status_manifest_matches_trusted_policy(self):
        gateway = PermissionGateway(builtin_policies())
        providers = self.registry.providers("security.module.status")

        self.assertEqual(
            [provider.module_id for provider in providers],
            ["security.center"],
        )
        manifest = self.registry.get_module("security.center").manifest
        contract = next(
            item
            for item in manifest.capabilities
            if item.capability_id == "security.module.status"
        )
        self.assertEqual(
            contract.requested_permissions,
            ("security.read-status",),
        )
        self.assertEqual(contract.input_schema["properties"], {})
        self.assertEqual(contract.input_schema["required"], [])
        self.assertEqual(
            gateway.required_scopes("security.module.status"),
            {"security.read-status"},
        )

    def test_security_scan_manifest_matches_trusted_policy(self):
        gateway = PermissionGateway(builtin_policies())
        providers = self.registry.providers("security.files.scan")

        self.assertEqual(
            [provider.module_id for provider in providers],
            ["security.center"],
        )
        manifest = self.registry.get_module("security.center").manifest
        contract = next(
            item
            for item in manifest.capabilities
            if item.capability_id == "security.files.scan"
        )
        self.assertEqual(
            contract.requested_permissions,
            (
                "filesystem.read-metadata",
                "filesystem.read-content",
                "security.write-findings",
            ),
        )
        self.assertEqual(
            set(contract.input_schema["properties"]),
            {"resource_id", "relative_path"},
        )
        self.assertEqual(
            set(contract.input_schema["required"]),
            {"resource_id", "relative_path"},
        )
        self.assertFalse(contract.input_schema["additionalProperties"])
        self.assertEqual(
            gateway.required_scopes("security.files.scan"),
            {
                "filesystem.read-metadata",
                "filesystem.read-content",
                "security.write-findings",
            },
        )

    def test_security_profile_and_finding_policies_match_manifest(self):
        gateway = PermissionGateway(builtin_policies())
        manifest = self.registry.get_module("security.center").manifest
        contracts = {
            item.capability_id: item for item in manifest.capabilities
        }

        profile = contracts["security.scan.run"]
        self.assertEqual(
            set(profile.requested_permissions),
            {
                "filesystem.read-metadata",
                "filesystem.read-content",
                "security.write-findings",
            },
        )
        self.assertEqual(
            gateway.required_scopes("security.scan.run"),
            set(profile.requested_permissions),
        )
        findings = contracts["security.findings.list"]
        self.assertEqual(findings.requested_permissions, ("security.read-findings",))
        self.assertEqual(
            gateway.required_scopes("security.findings.list"),
            {"security.read-findings"},
        )

        posture = contracts["security.posture.scan"]
        self.assertEqual(posture.requested_permissions, ("security.read-posture",))
        self.assertEqual(posture.input_schema["properties"], {})
        self.assertEqual(
            gateway.required_scopes("security.posture.scan"),
            {"security.read-posture"},
        )
        quarantine_list = contracts["security.quarantine.list"]
        self.assertEqual(
            quarantine_list.requested_permissions, ("security.read-findings",)
        )
        self.assertEqual(
            gateway.required_scopes("security.quarantine.list"),
            {"security.read-findings"},
        )


if __name__ == "__main__":
    unittest.main()
