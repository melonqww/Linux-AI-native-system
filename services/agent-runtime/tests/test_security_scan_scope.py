import unittest
from pathlib import Path

from ai_native_linux.cli import (
    combine_security_scans,
    security_quick_relative_paths,
    security_scan_configuration,
)


class SecurityScanScopeTests(unittest.TestCase):
    def test_xdg_quick_locations_stay_inside_home(self):
        home = Path("/home/tester").resolve(strict=False)

        paths = security_quick_relative_paths(
            home,
            '\n'.join((
                'XDG_DOWNLOAD_DIR="$HOME/Загрузки"',
                'XDG_DESKTOP_DIR="/etc"',
            )),
        )

        self.assertIn("Загрузки", paths)
        self.assertNotIn("/etc", paths)

    def test_configuration_uses_home_and_existing_risk_locations(self):
        home = Path.home().resolve(strict=True)
        temporary = Path(Path.cwd().anchor).resolve(strict=True)
        roots, quick = security_scan_configuration(home, temporary)

        self.assertEqual(roots["home"], home)
        self.assertEqual(set(roots), {"home", "temporary"})
        self.assertIn(("temporary", ""), quick)
        self.assertTrue(
            set(quick).issubset(
                {
                    ("home", "Downloads"),
                    ("home", "Desktop"),
                    ("home", ".config/autostart"),
                    ("temporary", ""),
                }
            )
        )

    def test_combined_scan_is_fail_closed_and_sums_counters(self):
        result = combine_security_scans(
            "full",
            "full",
            [
                {
                    "status": "completed",
                    "scanned_files": 3,
                    "scanned_bytes": 30,
                    "threat_files": 0,
                    "unknown_files": 0,
                    "skipped_files": 0,
                },
                {
                    "status": "partial",
                    "scanned_files": 2,
                    "scanned_bytes": 20,
                    "threat_files": 1,
                    "unknown_files": 1,
                    "skipped_files": 1,
                },
            ],
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["verdict"], "malware_detected")
        self.assertEqual(result["scanned_files"], 5)
        self.assertEqual(result["scopes_scanned"], 2)
        self.assertEqual(
            combine_security_scans("quick", "quick", [])["verdict"], "unknown"
        )


if __name__ == "__main__":
    unittest.main()
