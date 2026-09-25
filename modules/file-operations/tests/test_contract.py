import json
import unittest
from pathlib import Path

from ai_native_capabilities import load_manifest
from ai_native_permissions import PermissionGateway, builtin_policies
from ai_native_turns import CapabilityCandidateRouter, CapabilityDescriptor


MODULE_ROOT = Path(__file__).resolve().parents[1]


class FileOperationsContractTests(unittest.TestCase):
    def test_followup_examples_route_inspection_and_trash(self):
        router = CapabilityCandidateRouter(
            CapabilityDescriptor(
                contract.capability_id,
                contract.user_intent.operation,
                contract.user_intent.description,
                contract.user_intent.examples,
            )
            for contract in self.manifest.capabilities
            if contract.user_intent is not None
        )

        self.assertEqual(
            router.required_operations("Отправь найденный файл в корзину"),
            ("trash_results",),
        )
        self.assertEqual(
            router.required_operations("Покажи размер и сведения о найденном файле"),
            ("inspect_files",),
        )

    def setUp(self):
        self.payload = json.loads(
            (MODULE_ROOT / "module.json").read_text(encoding="utf-8")
        )
        self.manifest = load_manifest(MODULE_ROOT / "module.json")
        self.gateway = PermissionGateway(builtin_policies())

    def test_contract_is_enabled_with_registered_handlers(self):
        self.assertEqual(self.manifest.module_id, "files.operations")
        self.assertEqual(self.manifest.module_version, "0.3.0")
        self.assertTrue(self.manifest.default_enabled)
        self.assertEqual(self.manifest.dependencies, ("storage.catalog",))
        self.assertEqual(self.manifest.optional_dependencies, ("documents.query",))

    def test_r1_exposes_only_bounded_operations(self):
        contracts = {item.capability_id: item for item in self.manifest.capabilities}
        self.assertEqual(
            set(contracts),
            {
                "files.items.inspect",
                "files.directory.create",
                "files.items.move",
                "files.items.rename",
                "files.items.trash",
            },
        )
        operations = {
            item.user_intent.operation
            for item in contracts.values()
            if item.user_intent is not None
        }
        self.assertEqual(
            operations,
            {
                "inspect_files",
                "create_directory",
                "move_results",
                "rename_item",
                "trash_results",
            },
        )
        encoded = json.dumps(self.payload, ensure_ascii=False).casefold()
        self.assertNotIn("permanent_delete", encoded)
        self.assertNotIn("absolute_path", encoded)

    def test_existing_items_use_server_owned_references(self):
        for capability in self.payload["capabilities"]:
            properties = capability["input_schema"]["properties"]
            self.assertNotIn("path", properties)
            self.assertNotIn("paths", properties)
            if capability["id"] != "files.directory.create":
                self.assertIn("results_from", properties)
                self.assertIn("results_from", capability["input_schema"]["required"])

    def test_manifest_arguments_and_scopes_match_trusted_policy(self):
        policies = {item.capability_id: item for item in builtin_policies()}
        for contract in self.manifest.capabilities:
            policy = policies[contract.capability_id]
            self.assertEqual(
                set(contract.input_schema["properties"]),
                set(policy.allowed_arguments),
            )
            self.assertTrue(
                set(policy.required_arguments)
                <= set(contract.input_schema["required"])
            )
            self.assertTrue(
                self.gateway.required_scopes(contract.capability_id)
                <= set(contract.requested_permissions)
            )

    def test_only_inspection_is_r0_and_every_mutation_requires_approval(self):
        policies = {item.capability_id: item for item in builtin_policies()}
        inspect = policies["files.items.inspect"]
        self.assertEqual(inspect.risk.value, "R0")
        self.assertFalse(inspect.plan_approval_required)

        for capability_id in {
            "files.directory.create",
            "files.items.move",
            "files.items.rename",
            "files.items.trash",
        }:
            policy = policies[capability_id]
            self.assertEqual(policy.risk.value, "R1")
            self.assertTrue(policy.plan_approval_required)
            self.assertTrue(any(phase.approval_required for phase in policy.phases))


if __name__ == "__main__":
    unittest.main()
