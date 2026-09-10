import json
import unittest
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]


class SecurityCenterManifestTests(unittest.TestCase):
    def test_manifest_matches_the_foundation_contract(self) -> None:
        manifest = json.loads(
            (MODULE_ROOT / "module.json").read_text(encoding="utf-8")
        )

        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["module_id"], "security.center")
        self.assertEqual(manifest["module_version"], "0.1.0")
        self.assertEqual(manifest["lifecycle"], "on-demand")
        self.assertTrue(manifest["default_enabled"])
        self.assertEqual(manifest["requested_permissions"], ["security.read-status"])
        self.assertEqual(len(manifest["capabilities"]), 1)

        capability = manifest["capabilities"][0]
        self.assertEqual(capability["id"], "security.module.status")
        self.assertNotIn("user_intent", capability)
        self.assertEqual(
            capability["input_schema"],
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        )
        self.assertEqual(
            capability["requested_permissions"], ["security.read-status"]
        )

    def test_entrypoint_and_package_metadata_are_consistent(self) -> None:
        manifest = json.loads(
            (MODULE_ROOT / "module.json").read_text(encoding="utf-8")
        )

        self.assertEqual(
            manifest["entrypoint"],
            {
                "kind": "python-service",
                "module": "ai_native_security",
                "python_path": "src",
            },
        )
        self.assertTrue((MODULE_ROOT / "src" / "ai_native_security" / "__init__.py").is_file())


if __name__ == "__main__":
    unittest.main()
