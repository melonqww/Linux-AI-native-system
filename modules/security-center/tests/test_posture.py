import subprocess
from pathlib import Path
import shutil
import unittest
from uuid import uuid4

from ai_native_security import UbuntuPostureCollector


MODULE_ROOT = Path(__file__).resolve().parents[1]


class UbuntuPostureCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = MODULE_ROOT / f"security-posture-{uuid4().hex}"
        self.root.mkdir()
        self.apparmor = self.root / "apparmor-enabled"
        self.proc = self.root / "proc-net"
        self.proc.mkdir()
        self.system_autostart = self.root / "system-autostart"
        self.user_autostart = self.root / "user-autostart"
        self.system_autostart.mkdir()
        self.user_autostart.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_non_linux_platform_is_explicitly_unsupported(self) -> None:
        collector = UbuntuPostureCollector(platform="win32")

        self.assertEqual(
            collector.scan(),
            {
                "schema_version": 1,
                "supported": False,
                "status": "unsupported",
                "verdict": "unknown",
                "observations": [],
            },
        )

    def test_six_checks_are_bounded_and_report_findings(self) -> None:
        self.apparmor.write_text("Y\n", encoding="ascii")
        (self.proc / "tcp").write_text(
            "header\n0: 00000000:0016 00000000:0000 0A rest\n",
            encoding="ascii",
        )
        (self.proc / "tcp6").write_text("header\n", encoding="ascii")
        (self.user_autostart / "example.desktop").write_text("[Desktop Entry]")

        def run(command, **_kwargs):
            if command[0].endswith("apt-get"):
                stdout = "Inst openssl [1] (2 Ubuntu:jammy-security [amd64])\n"
            else:
                stdout = "Status: inactive\n"
            return subprocess.CompletedProcess(command, 0, stdout, "")

        collector = UbuntuPostureCollector(
            platform="linux",
            run_fn=run,
            apparmor_enabled=self.apparmor,
            proc_net=self.proc,
            system_autostart=self.system_autostart,
            user_autostart=self.user_autostart,
            apt_get=Path("/usr/bin/apt-get"),
            ufw=Path("/usr/sbin/ufw"),
            signature_version="builtin-v1",
            clamd_configured=True,
        )

        result = collector.scan()
        by_id = {item["check_id"]: item for item in result["observations"]}

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["verdict"], "findings_detected")
        self.assertEqual(len(by_id), 6)
        self.assertEqual(by_id["security_updates"]["state"], "finding")
        self.assertEqual(by_id["firewall"]["evidence_code"], "firewall_inactive")
        self.assertEqual(by_id["apparmor"]["state"], "healthy")
        self.assertEqual(by_id["listening_ports"]["count"], 1)
        self.assertEqual(by_id["autostart"]["count"], 1)
        self.assertEqual(by_id["scanner_rules"]["count"], 2)

    def test_missing_probe_sources_are_partial_never_clean(self) -> None:
        collector = UbuntuPostureCollector(
            platform="linux",
            apparmor_enabled=self.root / "missing-apparmor",
            proc_net=self.root / "missing-proc",
            system_autostart=self.root / "missing-autostart",
            apt_get=Path("/missing/apt-get"),
            ufw=Path("/missing/ufw"),
            run_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()),
            signature_version="builtin-v1",
        )

        result = collector.scan()

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["verdict"], "unknown")
        self.assertTrue(
            any(item["state"] == "unknown" for item in result["observations"])
        )


if __name__ == "__main__":
    unittest.main()
