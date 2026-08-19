"""Bounded, JSON-safe contracts returned by the system monitor module."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CpuMetrics:
    usage_percent: float | None
    logical_cpus: int
    load_average: tuple[float, float, float] | None
    temperature_celsius: float | None


@dataclass(frozen=True)
class MemoryMetrics:
    total_bytes: int
    available_bytes: int
    used_bytes: int
    usage_percent: float | None
    swap_total_bytes: int
    swap_used_bytes: int


@dataclass(frozen=True)
class BatteryMetrics:
    present: bool
    percent: float | None
    status: str
    temperature_celsius: float | None


@dataclass(frozen=True)
class DiskMetrics:
    mount_point: str
    filesystem: str
    source: str
    total_bytes: int
    free_bytes: int
    used_bytes: int
    usage_percent: float | None


@dataclass(frozen=True)
class ProcessMetrics:
    pid: int
    name: str
    state: str
    cpu_percent: float
    memory_bytes: int


@dataclass(frozen=True)
class ThermalSensor:
    name: str
    temperature_celsius: float


@dataclass(frozen=True)
class SystemSnapshot:
    schema_version: int
    supported: bool
    sampled_at_monotonic: float
    uptime_seconds: float | None
    cpu: CpuMetrics
    memory: MemoryMetrics
    battery: BatteryMetrics
    disks: tuple[DiskMetrics, ...]
    processes: tuple[ProcessMetrics, ...]
    thermal_sensors: tuple[ThermalSensor, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
