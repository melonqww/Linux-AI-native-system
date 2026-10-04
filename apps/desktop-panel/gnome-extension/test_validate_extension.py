import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "extension_validator", Path(__file__).with_name("validate_extension.py")
)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class InstalledAssetsTest(unittest.TestCase):
    def test_installed_assets_match_and_detect_missing_changed_or_stale_files(self):
        for condition in ("matching", "missing", "changed", "stale"):
            with self.subTest(condition=condition), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source" / "software-icons"
                installed = root / "installed" / "software-icons"
                source.mkdir(parents=True)
                installed.mkdir(parents=True)
                (source / "code.png").write_bytes(b"current icon")
                (installed / "code.png").write_bytes(b"current icon")
                if condition == "missing":
                    (installed / "code.png").unlink()
                elif condition == "changed":
                    (installed / "code.png").write_bytes(b"old icon")
                elif condition == "stale":
                    (installed / "old.svg").write_bytes(b"old icon")
                with contextlib.redirect_stdout(io.StringIO()):
                    result = validator.validate_assets(source.parent, installed.parent)
                self.assertEqual(result, condition == "matching")

    def test_missing_repository_assets_fail_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(validator.validate_assets(Path(directory) / "missing"))
