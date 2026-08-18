import ast
import importlib
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class RepositorySafetyTests(unittest.TestCase):
    def test_every_module_has_manifest_package_tests_and_safe_entrypoint(self) -> None:
        manifests = sorted(PROJECT_ROOT.glob("services/*/module.json")) + sorted(
            PROJECT_ROOT.glob("modules/*/module.json")
        )
        self.assertGreaterEqual(len(manifests), 6)
        module_ids: set[str] = set()
        for manifest_path in manifests:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            module_id = payload["module_id"]
            self.assertNotIn(module_id, module_ids)
            module_ids.add(module_id)
            root = manifest_path.parent
            self.assertTrue((root / "pyproject.toml").is_file(), module_id)
            self.assertTrue((root / "tests").is_dir(), module_id)
            python_path = payload["entrypoint"]["python_path"]
            resolved = (root / python_path).resolve(strict=True)
            resolved.relative_to(root.resolve(strict=True))
            importlib.import_module(payload["entrypoint"]["module"])
        for manifest_path in manifests:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            for dependency in payload["dependencies"]:
                self.assertIn(dependency, module_ids, f"{payload['module_id']} -> {dependency}")

    def test_production_python_has_no_direct_shell_escape(self) -> None:
        violations: list[str] = []
        for domain in ("services", "modules"):
            for path in (PROJECT_ROOT / domain).glob("*/src/**/*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    name = self._call_name(node.func)
                    if name in {"os.system", "os.popen"}:
                        violations.append(f"{path}:{node.lineno}:{name}")
                    if name.startswith("subprocess.") and any(
                        keyword.arg == "shell"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                        for keyword in node.keywords
                    ):
                        violations.append(f"{path}:{node.lineno}:shell=True")
        self.assertEqual(violations, [])

    @staticmethod
    def _call_name(node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = RepositorySafetyTests._call_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return ""


if __name__ == "__main__":
    unittest.main()
