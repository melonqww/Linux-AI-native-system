"""Dependency-free and bounded Linux metric collection."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from time import monotonic

from .contracts import (
    BatteryMetrics,
    CpuMetrics,
    DiskMetrics,
    MemoryMetrics,
    ProcessMetrics,
    SystemSnapshot,
    ThermalSensor,
)


_PSEUDO_FILESYSTEMS = frozenset(
    {
        "autofs",
        "bpf",
        "cgroup",
        "cgroup2",
        "configfs",
        "debugfs",
        "devpts",
        "devtmpfs",
        "efivarfs",
        "fusectl",
        "hugetlbfs",
        "mqueue",
        "overlay",
        "proc",
        "pstore",
        "ramfs",
        "securityfs",
        "sysfs",
        "tmpfs",
        "tracefs",
    }
)
_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")
_PROCESS_LIMIT = 50
_DISK_LIMIT = 32
_SENSOR_LIMIT = 16


class LinuxSystemCollector:
    def __init__(
        self,
        *,
        proc_root: Path = Path("/proc"),
        sys_root: Path = Path("/sys"),
        platform: str | None = None,
        stat_fn: Callable[[str], object] | None = None,
        statvfs_fn: Callable[[str], object] | None = None,
        cpu_count_fn: Callable[[], int | None] | None = None,
        load_average_fn: Callable[[], tuple[float, float, float]] | None = None,
        page_size: int | None = None,
    ) -> None:
        self.proc_root = proc_root
        self.sys_root = sys_root
        self.platform = platform or sys.platform
        self._stat_fn = stat_fn or os.stat
        self._statvfs_fn = statvfs_fn or getattr(os, "statvfs", None)
        self._cpu_count_fn = cpu_count_fn or os.cpu_count
        self._load_average_fn = load_average_fn or getattr(os, "getloadavg", None)
        self._configured_page_size = page_size
        self._previous_cpu: tuple[int, int] | None = None
        self._previous_process_ticks: dict[int, int] = {}
        self._last_system_delta = 0

    def snapshot(self, *, process_limit: int = 20) -> SystemSnapshot:
        if isinstance(process_limit, bool) or not isinstance(process_limit, int):
            raise ValueError("process_limit must be an integer")
        if not 1 <= process_limit <= _PROCESS_LIMIT:
            raise ValueError("process_limit must be between 1 and 50")
        sampled_at = monotonic()
        if not self.platform.startswith("linux"):
            return self._unsupported_snapshot(sampled_at)

        warnings: list[str] = []
        sensors = self._safe("thermal", self._thermal_sensors, (), warnings)
        cpu = self._safe("cpu", lambda: self._cpu(sensors), self._empty_cpu(), warnings)
        memory = self._safe("memory", self._memory, self._empty_memory(), warnings)
        battery = self._safe("battery", self._battery, self._empty_battery(), warnings)
        disks = self._safe("disks", self._disks, (), warnings)
        processes = self._safe(
            "processes",
            lambda: self._processes(cpu.logical_cpus, process_limit),
            (),
            warnings,
        )
        uptime = self._safe("uptime", self._uptime, None, warnings)
        return SystemSnapshot(
            schema_version=1,
            supported=True,
            sampled_at_monotonic=sampled_at,
            uptime_seconds=uptime,
            cpu=cpu,
            memory=memory,
            battery=battery,
            disks=disks,
            processes=processes,
            thermal_sensors=sensors,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _safe(label: str, operation, fallback, warnings: list[str]):
        try:
            return operation()
        except (OSError, UnicodeError, ValueError, IndexError):
            warnings.append(f"{label}_unavailable")
            return fallback

    def _cpu(self, sensors: tuple[ThermalSensor, ...]) -> CpuMetrics:
        first_line = (self.proc_root / "stat").read_text(encoding="utf-8").splitlines()[0]
        fields = first_line.split()
        if not fields or fields[0] != "cpu" or len(fields) < 5:
            raise ValueError("invalid /proc/stat")
        values = tuple(int(value) for value in fields[1:])
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        total = sum(values)
        usage: float | None = None
        if self._previous_cpu is not None:
            previous_total, previous_idle = self._previous_cpu
            total_delta = total - previous_total
            idle_delta = idle - previous_idle
            if total_delta > 0:
                usage = self._percent(total_delta - idle_delta, total_delta)
                self._last_system_delta = total_delta
            else:
                self._last_system_delta = 0
        self._previous_cpu = (total, idle)
        try:
            load_average = (
                tuple(round(value, 2) for value in self._load_average_fn())
                if self._load_average_fn is not None
                else None
            )
        except OSError:
            load_average = None
        cpu_temperatures = [
            sensor.temperature_celsius
            for sensor in sensors
            if any(
                token in sensor.name.casefold()
                for token in ("cpu", "core", "package", "x86_pkg", "k10temp", "soc")
            )
        ]
        return CpuMetrics(
            usage_percent=usage,
            logical_cpus=max(1, self._cpu_count_fn() or 1),
            load_average=load_average,
            temperature_celsius=max(cpu_temperatures, default=None),
        )

    def _memory(self) -> MemoryMetrics:
        values: dict[str, int] = {}
        for line in (self.proc_root / "meminfo").read_text(encoding="utf-8").splitlines():
            key, separator, raw = line.partition(":")
            if not separator:
                continue
            parts = raw.split()
            if parts:
                values[key] = int(parts[0]) * 1024
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", values.get("MemFree", 0))
        if total <= 0:
            raise ValueError("MemTotal is missing")
        available = max(0, min(total, available))
        swap_total = max(0, values.get("SwapTotal", 0))
        swap_free = max(0, min(swap_total, values.get("SwapFree", 0)))
        used = total - available
        return MemoryMetrics(
            total_bytes=total,
            available_bytes=available,
            used_bytes=used,
            usage_percent=self._percent(used, total),
            swap_total_bytes=swap_total,
            swap_used_bytes=swap_total - swap_free,
        )

    def _battery(self) -> BatteryMetrics:
        power_root = self.sys_root / "class" / "power_supply"
        batteries: list[tuple[float, str, float | None]] = []
        for path in sorted(power_root.iterdir()) if power_root.is_dir() else ():
            if self._read_optional(path / "type").casefold() != "battery":
                continue
            capacity = self._number_optional(path / "capacity")
            status = self._read_optional(path / "status").casefold() or "unknown"
            temperature = self._temperature_value(path / "temp")
            if capacity is not None:
                batteries.append((max(0.0, min(100.0, capacity)), status, temperature))
        if not batteries:
            return self._empty_battery()
        percent = round(sum(item[0] for item in batteries) / len(batteries), 1)
        statuses = {item[1] for item in batteries}
        status = statuses.pop() if len(statuses) == 1 else "mixed"
        temperatures = [item[2] for item in batteries if item[2] is not None]
        return BatteryMetrics(
            present=True,
            percent=percent,
            status=status,
            temperature_celsius=max(temperatures, default=None),
        )

    def _thermal_sensors(self) -> tuple[ThermalSensor, ...]:
        sensors: list[ThermalSensor] = []
        seen: set[tuple[str, float]] = set()

        def append(name: str, temperature: float | None) -> None:
            if temperature is None or not -20 <= temperature <= 150:
                return
            item = (name[:80], temperature)
            if item in seen or len(sensors) >= _SENSOR_LIMIT:
                return
            seen.add(item)
            sensors.append(ThermalSensor(name=item[0], temperature_celsius=temperature))

        thermal_root = self.sys_root / "class" / "thermal"
        for path in sorted(thermal_root.glob("thermal_zone*")):
            name = self._read_optional(path / "type") or path.name
            append(name, self._temperature_value(path / "temp"))
            if len(sensors) >= _SENSOR_LIMIT:
                break
        hwmon_root = self.sys_root / "class" / "hwmon"
        for device in sorted(hwmon_root.glob("hwmon*")):
            device_name = self._read_optional(device / "name") or device.name
            for sensor_path in sorted(device.glob("temp*_input")):
                stem = sensor_path.name.removesuffix("_input")
                label = self._read_optional(device / f"{stem}_label")
                append(
                    f"{device_name} {label or stem}".strip(),
                    self._temperature_value(sensor_path),
                )
                if len(sensors) >= _SENSOR_LIMIT:
                    break
            if len(sensors) >= _SENSOR_LIMIT:
                break
        return tuple(sensors)

    def _disks(self) -> tuple[DiskMetrics, ...]:
        mountinfo = (self.proc_root / "self" / "mountinfo").read_text(encoding="utf-8")
        candidates: list[tuple[str, str, str]] = []
        for line in mountinfo.splitlines():
            left, separator, right = line.partition(" - ")
            if not separator:
                continue
            left_fields = left.split()
            right_fields = right.split()
            if len(left_fields) < 5 or len(right_fields) < 2:
                continue
            mount_point = self._unescape_mount(left_fields[4])
            filesystem = right_fields[0]
            source = self._unescape_mount(right_fields[1])
            if filesystem in _PSEUDO_FILESYSTEMS or not mount_point.startswith("/"):
                continue
            candidates.append((mount_point, filesystem, source))

        disks: list[DiskMetrics] = []
        seen_devices: set[int] = set()
        for mount_point, filesystem, source in sorted(candidates, key=lambda item: (len(item[0]), item[0])):
            try:
                device = self._stat_fn(mount_point).st_dev
                if self._statvfs_fn is None:
                    raise OSError("statvfs is unavailable")
                stats = self._statvfs_fn(mount_point)
            except (AttributeError, OSError):
                continue
            if device in seen_devices:
                continue
            seen_devices.add(device)
            total = stats.f_blocks * stats.f_frsize
            free = stats.f_bavail * stats.f_frsize
            if total <= 0:
                continue
            used = max(0, total - free)
            disks.append(
                DiskMetrics(
                    mount_point=mount_point[:512],
                    filesystem=filesystem[:40],
                    source=source[:512],
                    total_bytes=total,
                    free_bytes=max(0, free),
                    used_bytes=used,
                    usage_percent=self._percent(used, total),
                )
            )
            if len(disks) >= _DISK_LIMIT:
                break
        return tuple(disks)

    def _processes(self, logical_cpus: int, limit: int) -> tuple[ProcessMetrics, ...]:
        system_delta = self._last_system_delta
        current_ticks: dict[int, int] = {}
        processes: list[ProcessMetrics] = []
        for path in self.proc_root.iterdir():
            if not path.name.isdecimal():
                continue
            try:
                pid = int(path.name)
                stat = (path / "stat").read_text(encoding="utf-8")
                close = stat.rfind(")")
                open_parenthesis = stat.find("(")
                if open_parenthesis < 0 or close <= open_parenthesis:
                    continue
                name = stat[open_parenthesis + 1 : close]
                fields = stat[close + 2 :].split()
                state = fields[0]
                ticks = int(fields[11]) + int(fields[12])
                memory_pages = int((path / "statm").read_text(encoding="utf-8").split()[1])
                memory_bytes = max(0, memory_pages * self._page_size())
            except (OSError, UnicodeError, ValueError, IndexError):
                continue
            current_ticks[pid] = ticks
            process_delta = max(0, ticks - self._previous_process_ticks.get(pid, ticks))
            cpu_percent = 0.0
            if system_delta > 0:
                cpu_percent = min(100.0, round(100.0 * process_delta / system_delta, 1))
            processes.append(
                ProcessMetrics(
                    pid=pid,
                    name=(name or f"PID {pid}")[:120],
                    state=state[:1],
                    cpu_percent=cpu_percent,
                    memory_bytes=memory_bytes,
                )
            )
        self._previous_process_ticks = current_ticks
        processes.sort(key=lambda item: (-item.cpu_percent, -item.memory_bytes, item.pid))
        return tuple(processes[:limit])

    def _uptime(self) -> float:
        return max(0.0, float((self.proc_root / "uptime").read_text(encoding="utf-8").split()[0]))

    @staticmethod
    def _percent(numerator: int, denominator: int) -> float | None:
        if denominator <= 0:
            return None
        return round(max(0.0, min(100.0, numerator * 100.0 / denominator)), 1)

    def _page_size(self) -> int:
        if self._configured_page_size is not None:
            return max(1, self._configured_page_size)
        try:
            return int(getattr(os, "sysconf")("SC_PAGE_SIZE"))
        except (AttributeError, OSError, ValueError):
            return 4096

    @staticmethod
    def _read_optional(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return ""

    @classmethod
    def _number_optional(cls, path: Path) -> float | None:
        raw = cls._read_optional(path)
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    @classmethod
    def _temperature_value(cls, path: Path) -> float | None:
        value = cls._number_optional(path)
        if value is None:
            return None
        if abs(value) > 1_000:
            value /= 1_000.0
        elif abs(value) > 150:
            value /= 10.0
        return round(value, 1)

    @staticmethod
    def _unescape_mount(value: str) -> str:
        return _MOUNT_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)

    def _empty_cpu(self) -> CpuMetrics:
        return CpuMetrics(None, max(1, self._cpu_count_fn() or 1), None, None)

    @staticmethod
    def _empty_memory() -> MemoryMetrics:
        return MemoryMetrics(0, 0, 0, None, 0, 0)

    @staticmethod
    def _empty_battery() -> BatteryMetrics:
        return BatteryMetrics(False, None, "not_present", None)

    def _unsupported_snapshot(self, sampled_at: float) -> SystemSnapshot:
        return SystemSnapshot(
            schema_version=1,
            supported=False,
            sampled_at_monotonic=sampled_at,
            uptime_seconds=None,
            cpu=self._empty_cpu(),
            memory=self._empty_memory(),
            battery=self._empty_battery(),
            disks=(),
            processes=(),
            thermal_sensors=(),
            warnings=("platform_unsupported",),
        )
