from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "security_center_scan", ROOT / "security_center_scan.py"
)
assert SPEC is not None and SPEC.loader is not None
SCAN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCAN)


class SecurityCenterNautilusTests(unittest.TestCase):
    def test_local_target_is_converted_to_home_relative_path(self) -> None:
        sample = ROOT / "security_center_scan.py"
        self.assertEqual(
            SCAN.resolve_target(sample.as_uri(), ROOT),
            ("file", "security_center_scan.py"),
        )

    def test_directory_and_outside_home_boundaries(self) -> None:
        self.assertEqual(
            SCAN.resolve_target(ROOT.as_uri(), ROOT.parent),
            ("folder", "nautilus"),
        )
        outside = ROOT.parent / "gnome-extension"
        with self.assertRaisesRegex(ValueError, "target_outside_home"):
            SCAN.resolve_target(outside.as_uri(), ROOT)

    def test_result_messages_cover_clean_and_threats(self) -> None:
        self.assertIn(
            "Угроз не обнаружено",
            SCAN.result_message(
                {
                    "status": "completed",
                    "verdict": "no_threat_detected",
                    "scanned_files": 4,
                }
            ),
        )
        self.assertIn(
            "Обнаружено угроз: 2",
            SCAN.result_message({"threat_files": 2, "scanned_files": 5}),
        )

    def test_menu_provider_uses_official_nautilus_api(self) -> None:
        source = (ROOT / "security_center_nautilus.py").read_text(encoding="utf-8")
        self.assertIn("Nautilus.MenuProvider", source)
        self.assertIn("Gio.Subprocess.new", source)
        self.assertIn("Проверить с помощью Security Center", source)


if __name__ == "__main__":
    unittest.main()
