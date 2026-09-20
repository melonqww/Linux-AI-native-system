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
        self.assertEqual(manifest["module_version"], "1.0.0")
        self.assertEqual(manifest["lifecycle"], "on-demand")
        self.assertTrue(manifest["default_enabled"])
        self.assertEqual(
            manifest["requested_permissions"],
            [
                "security.read-status",
                "filesystem.read-metadata",
                "filesystem.read-content",
                "filesystem.write-content",
                "security.write-findings",
                "security.read-findings",
                "security.read-posture",
                "security.write-quarantine",
            ],
        )
        self.assertEqual(len(manifest["capabilities"]), 9)

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

        scan = manifest["capabilities"][1]
        self.assertEqual(scan["id"], "security.files.scan")
        self.assertNotIn("user_intent", scan)
        self.assertEqual(
            scan["input_schema"]["required"], ["resource_id", "relative_path"]
        )
        self.assertFalse(scan["input_schema"]["additionalProperties"])
        self.assertEqual(
            scan["requested_permissions"],
            [
                "filesystem.read-metadata",
                "filesystem.read-content",
                "security.write-findings",
            ],
        )

        profile = manifest["capabilities"][2]
        self.assertEqual(profile["id"], "security.scan.run")
        self.assertEqual(profile["input_schema"]["required"], ["resource_id", "mode"])
        self.assertIn("relative_path", profile["input_schema"]["properties"])
        self.assertEqual(profile["input_schema"]["properties"]["mode"]["enum"], ["quick", "full"])
        self.assertFalse(profile["input_schema"]["additionalProperties"])

        findings = manifest["capabilities"][3]
        self.assertEqual(findings["id"], "security.findings.list")
        self.assertEqual(findings["input_schema"]["required"], [])
        self.assertFalse(findings["input_schema"]["additionalProperties"])
        self.assertEqual(
            findings["input_schema"]["properties"]["limit"]["maximum"], 25
        )
        self.assertEqual(findings["requested_permissions"], ["security.read-findings"])

        posture = manifest["capabilities"][4]
        self.assertEqual(posture["id"], "security.posture.scan")
        self.assertEqual(posture["input_schema"]["properties"], {})
        self.assertEqual(posture["input_schema"]["required"], [])
        self.assertFalse(posture["input_schema"]["additionalProperties"])
        self.assertEqual(posture["requested_permissions"], ["security.read-posture"])

        quarantine_list, prepare, commit, restore = manifest["capabilities"][5:]
        self.assertEqual(quarantine_list["id"], "security.quarantine.list")
        self.assertEqual(
            quarantine_list["requested_permissions"], ["security.read-findings"]
        )
        self.assertEqual(prepare["id"], "security.quarantine.prepare")
        self.assertEqual(prepare["input_schema"]["required"], ["finding_id"])
        self.assertEqual(commit["id"], "security.quarantine.commit")
        self.assertEqual(restore["id"], "security.quarantine.restore")
        self.assertEqual(commit["input_schema"]["required"], ["quarantine_id"])
        self.assertEqual(restore["input_schema"]["required"], ["quarantine_id"])

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
