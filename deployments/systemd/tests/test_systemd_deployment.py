import os
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SYSTEMD_ROOT = PROJECT_ROOT / "deployments" / "systemd"


class SystemdDeploymentTests(unittest.TestCase):
    def test_unit_has_restart_runtime_directory_and_basic_hardening(self) -> None:
        unit = (SYSTEMD_ROOT / "ai-native-linux-runtime.service").read_text(
            encoding="utf-8"
        )
        for marker in (
            "WantedBy=default.target",
            "Restart=on-failure",
            "RuntimeDirectory=ai-native-linux",
            "RuntimeDirectoryMode=0700",
            "UMask=0077",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "EnvironmentFile=-%h/.config/ai-native-linux/runtime.env",
            "ExecStart=/usr/bin/env bash %h/.local/libexec/ai-native-linux/run-runtime.sh",
        ):
            self.assertIn(marker, unit)
        self.assertNotIn("User=root", unit)
        self.assertNotIn("sudo", unit)

    def test_install_enables_service_and_verifies_socket(self) -> None:
        source = (SYSTEMD_ROOT / "install-user-service.sh").read_text(encoding="utf-8")
        for marker in (
            'python3 -m venv "${virtual_environment}"',
            '--requirement "${requirements_file}"',
            'sudo apt install python3-venv',
            "import pypdf",
            "systemctl --user daemon-reload",
            "systemctl --user reset-failed ai-native-linux-runtime.service",
            "systemctl --user enable --now ai-native-linux-runtime.service",
            '[[ -S "${socket_path}" ]]',
            "RESULT: PANEL CORE CONNECTED",
            "journalctl --user -u ai-native-linux-runtime.service",
        ):
            self.assertIn(marker, source)

    def test_runtime_requirements_include_pdf_dependency(self) -> None:
        requirements = (SYSTEMD_ROOT / "runtime-requirements.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("pypdf>=5,<7", requirements)

    def test_launcher_uses_isolated_runtime_python_and_preflights_dependencies(self) -> None:
        source = (SYSTEMD_ROOT / "run-runtime.sh").read_text(encoding="utf-8")
        self.assertIn('runtime_python="${runtime_data}/venv/bin/python"', source)
        self.assertIn("import pypdf", source)
        self.assertIn('exec "${runtime_python}" -m ai_native_linux.cli', source)

    def test_uninstall_preserves_runtime_data_and_configuration(self) -> None:
        source = (SYSTEMD_ROOT / "uninstall-user-service.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("disable --now ai-native-linux-runtime.service", source)
        self.assertIn('rm -f -- "${unit_path}" "${launcher_path}"', source)
        self.assertNotIn("runtime_config_directory", source)
        self.assertNotIn("XDG_DATA_HOME", source)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux bash")
    def test_shell_scripts_pass_bash_syntax_check(self) -> None:
        for filename in (
            "run-runtime.sh",
            "install-user-service.sh",
            "uninstall-user-service.sh",
        ):
            result = subprocess.run(
                ["bash", "-n", str(SYSTEMD_ROOT / filename)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, f"{filename}: {result.stderr}")

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires systemd tooling")
    def test_unit_passes_systemd_analyze_verify(self) -> None:
        analyzer = shutil.which("systemd-analyze")
        if analyzer is None:
            self.skipTest("systemd-analyze is not installed")
        result = subprocess.run(
            [analyzer, "--user", "verify", str(SYSTEMD_ROOT / "ai-native-linux-runtime.service")],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux execution")
    def test_launcher_builds_isolated_paths_and_runtime_arguments(self) -> None:
        root = PROJECT_ROOT / "tmp" / "systemd-launcher-tests" / str(uuid4())
        fake_home = root / "home"
        fake_config = root / "config"
        fake_data = root / "data"
        fake_repository = root / "repository with spaces"
        try:
            (fake_config / "ai-native-linux").mkdir(parents=True)
            runtime_python = fake_data / "ai-native-linux" / "venv" / "bin" / "python"
            runtime_python.parent.mkdir(parents=True)
            for relative in (
                "services/agent-runtime/src",
                "services/task-ledger/src",
                "services/permission-gateway/src",
                "services/execution-orchestrator/src",
                "services/intent-compiler/src",
                "services/capability-registry/src",
                "services/module-manager/src",
                "services/query-service/src",
                "services/storage-catalog/src",
                "services/indexer/src",
                "services/index-scheduler/src",
                "modules/documents-pdf/src",
                "modules/system-monitor/src",
            ):
                (fake_repository / relative).mkdir(parents=True)
            cli = fake_repository / "services/agent-runtime/src/ai_native_linux/cli.py"
            cli.parent.mkdir(parents=True, exist_ok=True)
            cli.write_text("# fixture\n", encoding="utf-8")
            (fake_config / "ai-native-linux" / "repository-root").write_text(
                f"{fake_repository}\n", encoding="utf-8"
            )
            runtime_python.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"$1\" == \"-c\" ]]; then exit 0; fi\n"
                "printf 'PYTHONPATH=%s\\n' \"${PYTHONPATH}\"\n"
                "printf 'ARG=%s\\n' \"$@\"\n",
                encoding="utf-8",
            )
            runtime_python.chmod(runtime_python.stat().st_mode | stat.S_IXUSR)
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(fake_home),
                    "XDG_CONFIG_HOME": str(fake_config),
                    "XDG_DATA_HOME": str(fake_data),
                    "PATH": "/usr/bin:/bin",
                    "PYTHONPATH": "existing-package-path",
                    "AI_NATIVE_INTENT_MODEL": "test-model:2b",
                }
            )
            result = subprocess.run(
                ["bash", str(SYSTEMD_ROOT / "run-runtime.sh")],
                capture_output=True,
                text=True,
                timeout=15,
                env=environment,
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{fake_repository}/modules/system-monitor/src", result.stdout)
        self.assertIn("existing-package-path", result.stdout)
        self.assertIn("ARG=--serve-panel", result.stdout)
        self.assertIn("ARG=--transport", result.stdout)
        self.assertIn("ARG=unix", result.stdout)
        self.assertIn("ARG=--intent-model", result.stdout)
        self.assertIn("ARG=test-model:2b", result.stdout)
        self.assertIn(f"{fake_data}/ai-native-linux/storage-catalog.sqlite3", result.stdout)


if __name__ == "__main__":
    unittest.main()
