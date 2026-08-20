import os
import shutil
import subprocess
import unittest
from pathlib import Path
from uuid import uuid4

from ai_native_system_updates import UbuntuUpdateChecker


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class SystemUpdatesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / "tmp" / "system-updates-tests" / str(uuid4())
        self.lists = self.root / "lists"
        self.lists.mkdir(parents=True)
        package_list = self.lists / "archive_ubuntu_dists_noble_InRelease"
        package_list.write_text("fixture", encoding="utf-8")
        os.utime(package_list, (9_900, 9_900))
        self.calls = []

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _checker(
        self, stdout: str, *, returncode: int = 0, now: float = 10_000
    ) -> UbuntuUpdateChecker:
        def run(command, **kwargs):
            self.calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, returncode, stdout, "")

        return UbuntuUpdateChecker(
            platform="linux",
            apt_get="/usr/bin/apt-get",
            lists_root=self.lists,
            run_fn=run,
            now_fn=lambda: now,
        )

    def test_reports_available_and_security_updates_without_mutating_apt(self) -> None:
        result = self._checker(
            "Inst openssl [1] (2 Ubuntu:24.04/noble-security [amd64])\n"
            "Inst firefox:amd64 [1] (2 Ubuntu:24.04/noble-updates [amd64])\n"
            "Conf openssl (2 Ubuntu:24.04/noble-security [amd64])\n"
        ).check()

        self.assertEqual(result.state, "updates_available")
        self.assertEqual(result.available_count, 2)
        self.assertEqual(result.security_count, 1)
        self.assertEqual(result.package_names, ("openssl", "firefox:amd64"))
        self.assertEqual(result.cache_age_seconds, 100)
        self.assertFalse(result.cache_stale)
        command, kwargs = self.calls[0]
        self.assertIn("--simulate", command)
        self.assertIn("Debug::NoLocking=1", command)
        self.assertNotIn("update", command)
        self.assertFalse(kwargs["check"])
        self.assertEqual(kwargs["timeout"], 20)

    def test_empty_fresh_cache_result_is_up_to_date(self) -> None:
        result = self._checker("").check()

        self.assertEqual(result.state, "up_to_date")
        self.assertEqual(result.available_count, 0)
        self.assertEqual(result.warnings, ())

    def test_stale_cache_is_explicit_and_never_claims_freshness(self) -> None:
        old = self.lists / "archive_ubuntu_dists_noble_InRelease"
        os.utime(old, (0, 0))

        result = self._checker("", now=100_000).check()

        self.assertTrue(result.cache_stale)
        self.assertIn("cache_stale", result.warnings)

    def test_failures_and_unsupported_platform_are_redacted(self) -> None:
        failed = self._checker("sensitive apt details", returncode=100).check()
        unsupported = UbuntuUpdateChecker(platform="win32", apt_get=None).check()

        self.assertEqual(failed.state, "unavailable")
        self.assertEqual(failed.warnings, ("check_failed",))
        self.assertFalse(unsupported.supported)
        self.assertEqual(unsupported.warnings, ("platform_unsupported",))


if __name__ == "__main__":
    unittest.main()
