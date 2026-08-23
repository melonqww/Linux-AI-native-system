import importlib.util
import os
import shutil
import stat
import unittest
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = PROJECT_ROOT / "deployments/systemd/migrate_runtime_env.py"
SPEC = importlib.util.spec_from_file_location("migrate_runtime_env", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RuntimeEnvironmentMigrationTests(unittest.TestCase):
    def setUp(self):
        self.root = PROJECT_ROOT / "tmp/runtime-env-tests" / str(uuid4())
        self.root.mkdir(parents=True)
        self.path = self.root / "runtime.env"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_creates_current_defaults_when_file_is_missing(self):
        changed = MODULE.migrate_runtime_env(self.path)

        self.assertTrue(changed)
        self.assertEqual(
            self.path.read_text(encoding="utf-8"),
            "AI_NATIVE_INTENT_MODEL=qwen3.5:2b\n"
            "AI_NATIVE_OLLAMA_URL=http://127.0.0.1:11434\n",
        )
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_migrates_only_the_known_legacy_default(self):
        self.path.write_text(
            "AI_NATIVE_INTENT_MODEL=qwen3:1.7b\n"
            "AI_NATIVE_OLLAMA_URL=http://127.0.0.1:11434\n"
            "CUSTOM_OPTION=preserved\n",
            encoding="utf-8",
        )

        changed = MODULE.migrate_runtime_env(self.path)

        self.assertTrue(changed)
        content = self.path.read_text(encoding="utf-8")
        self.assertIn("AI_NATIVE_INTENT_MODEL=qwen3.5:2b", content)
        self.assertIn("CUSTOM_OPTION=preserved", content)
        self.assertNotIn("qwen3:1.7b", content)

    def test_preserves_a_real_user_model_override(self):
        content = (
            "AI_NATIVE_INTENT_MODEL=private/model:custom\n"
            "AI_NATIVE_OLLAMA_URL=http://127.0.0.1:11434\n"
        )
        self.path.write_text(content, encoding="utf-8")

        changed = MODULE.migrate_runtime_env(self.path)

        self.assertFalse(changed)
        self.assertEqual(self.path.read_text(encoding="utf-8"), content)


if __name__ == "__main__":
    unittest.main()
