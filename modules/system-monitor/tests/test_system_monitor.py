import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from ai_native_system_monitor import LinuxSystemCollector


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class FakeStat:
    st_dev = 42


class FakeStatVfs:
    f_blocks = 1_000
    f_bavail = 400
    f_frsize = 4_096


class SystemMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / "tmp" / "system-monitor-tests" / str(uuid4())
        self.proc = self.root / "proc"
        self.sys = self.root / "sys"
        (self.proc / "self").mkdir(parents=True)
        (self.sys / "class" / "thermal" / "thermal_zone0").mkdir(parents=True)
        (self.sys / "class" / "power_supply" / "BAT0").mkdir(parents=True)
        self._write_common_files()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_common_files(self) -> None:
        (self.proc / "stat").write_text(
            "cpu  200 0 100 600 100 0 0 0 0 0\n", encoding="utf-8"
        )
        (self.proc / "meminfo").write_text(
            "MemTotal:       8000000 kB\n"
            "MemAvailable:   3000000 kB\n"
            "SwapTotal:      2000000 kB\n"
            "SwapFree:       1500000 kB\n",
            encoding="utf-8",
        )
        (self.proc / "uptime").write_text("90061.5 0.0\n", encoding="utf-8")
        (self.proc / "self" / "mountinfo").write_text(
            "24 1 8:1 / / rw - ext4 /dev/sda1 rw\n"
            "25 1 0:5 / /proc rw - proc proc rw\n",
            encoding="utf-8",
        )
        thermal = self.sys / "class" / "thermal" / "thermal_zone0"
        (thermal / "type").write_text("x86_pkg_temp\n", encoding="utf-8")
        (thermal / "temp").write_text("47000\n", encoding="utf-8")
        battery = self.sys / "class" / "power_supply" / "BAT0"
        (battery / "type").write_text("Battery\n", encoding="utf-8")
        (battery / "capacity").write_text("82\n", encoding="utf-8")
        (battery / "status").write_text("Discharging\n", encoding="utf-8")
        (battery / "temp").write_text("310\n", encoding="utf-8")
        self._write_process(ticks=120)

    def _write_process(self, *, ticks: int) -> None:
        process = self.proc / "123"
        process.mkdir(exist_ok=True)
        # fields after comm start at Linux stat field 3; indexes 11/12 are utime/stime.
        fields = ["S"] + ["0"] * 10 + [str(ticks - 20), "20"] + ["0"] * 20
        (process / "stat").write_text(
            f"123 (worker process) {' '.join(fields)}\n", encoding="utf-8"
        )
        (process / "statm").write_text("100 10 0 0 0 0 0\n", encoding="utf-8")

    def _collector(self) -> LinuxSystemCollector:
        return LinuxSystemCollector(
            proc_root=self.proc,
            sys_root=self.sys,
            platform="linux",
            stat_fn=lambda _path: FakeStat(),
            statvfs_fn=lambda _path: FakeStatVfs(),
            cpu_count_fn=lambda: 4,
            load_average_fn=lambda: (1.25, 0.5, 0.25),
            page_size=4096,
        )

    def test_collects_complete_snapshot_and_cpu_delta(self) -> None:
        collector = self._collector()
        first = collector.snapshot(process_limit=5)
        (self.proc / "stat").write_text(
            "cpu  250 0 110 640 100 0 0 0 0 0\n", encoding="utf-8"
        )
        self._write_process(ticks=130)
        second = collector.snapshot(process_limit=5)

        self.assertIsNone(first.cpu.usage_percent)
        self.assertEqual(second.cpu.usage_percent, 60.0)
        self.assertEqual(second.cpu.logical_cpus, 4)
        self.assertEqual(second.cpu.load_average, (1.25, 0.5, 0.25))
        self.assertEqual(second.cpu.temperature_celsius, 47.0)
        self.assertEqual(second.memory.usage_percent, 62.5)
        self.assertEqual(second.memory.swap_used_bytes, 500_000 * 1024)
        self.assertTrue(second.battery.present)
        self.assertEqual(second.battery.percent, 82.0)
        self.assertEqual(second.battery.status, "discharging")
        self.assertEqual(second.battery.temperature_celsius, 31.0)
        self.assertEqual(second.uptime_seconds, 90061.5)
        self.assertEqual(len(second.disks), 1)
        self.assertEqual(second.disks[0].mount_point, "/")
        self.assertEqual(second.disks[0].free_bytes, 400 * 4096)
        self.assertEqual(len(second.processes), 1)
        self.assertEqual(second.processes[0].name, "worker process")
        self.assertEqual(second.processes[0].cpu_percent, 10.0)
        self.assertEqual(second.processes[0].memory_bytes, 10 * 4096)
        self.assertEqual(second.warnings, ())

    def test_unsupported_platform_returns_stable_empty_snapshot(self) -> None:
        snapshot = LinuxSystemCollector(
            proc_root=self.proc, sys_root=self.sys, platform="win32"
        ).snapshot()

        self.assertFalse(snapshot.supported)
        self.assertEqual(snapshot.warnings, ("platform_unsupported",))
        self.assertEqual(snapshot.processes, ())
        self.assertEqual(snapshot.disks, ())

    def test_partial_sources_degrade_without_breaking_snapshot(self) -> None:
        (self.proc / "meminfo").unlink()
        collector = self._collector()

        snapshot = collector.snapshot()

        self.assertTrue(snapshot.supported)
        self.assertIn("memory_unavailable", snapshot.warnings)
        self.assertEqual(snapshot.memory.total_bytes, 0)
        self.assertEqual(snapshot.battery.percent, 82.0)

    def test_process_limit_is_closed_and_bounded(self) -> None:
        collector = self._collector()
        for invalid in (0, 51, True, "20"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    collector.snapshot(process_limit=invalid)

    def test_reads_hwmon_temperature_labels(self) -> None:
        hwmon = self.sys / "class" / "hwmon" / "hwmon0"
        hwmon.mkdir(parents=True)
        (hwmon / "name").write_text("coretemp\n", encoding="utf-8")
        (hwmon / "temp1_label").write_text("Package id 0\n", encoding="utf-8")
        (hwmon / "temp1_input").write_text("51000\n", encoding="utf-8")

        snapshot = self._collector().snapshot()

        self.assertIn("coretemp Package id 0", [item.name for item in snapshot.thermal_sensors])
        self.assertEqual(snapshot.cpu.temperature_celsius, 51.0)

    def test_snapshot_is_json_safe_and_does_not_expose_process_arguments(self) -> None:
        collector = self._collector()
        payload = collector.snapshot().to_dict()

        self.assertEqual(payload["schema_version"], 1)
        process = payload["processes"][0]
        self.assertEqual(set(process), {"pid", "name", "state", "cpu_percent", "memory_bytes"})
        self.assertNotIn("cmdline", process)
        self.assertNotIn("environment", process)


if __name__ == "__main__":
    unittest.main()
