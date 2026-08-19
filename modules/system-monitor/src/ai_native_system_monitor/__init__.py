"""First-party system.monitor worker entrypoint."""

from __future__ import annotations

from .collector import LinuxSystemCollector
from .contracts import (
    BatteryMetrics,
    CpuMetrics,
    DiskMetrics,
    MemoryMetrics,
    ProcessMetrics,
    SystemSnapshot,
    ThermalSensor,
)


_collector: LinuxSystemCollector | None = None


def worker_start() -> None:
    global _collector
    _collector = LinuxSystemCollector()
    # Prime CPU/process counters. The first public snapshot remains valid even
    # when the worker is queried immediately after startup.
    _collector.snapshot(process_limit=20)


def worker_health() -> dict[str, object]:
    return {"status": "ready", "platform_supported": _require_collector().platform.startswith("linux")}


def worker_invoke(operation: str, payload: dict[str, object]) -> dict[str, object]:
    if operation != "snapshot":
        raise ValueError("unknown_operation")
    if set(payload) - {"process_limit"}:
        raise ValueError("invalid_payload")
    process_limit = payload.get("process_limit", 20)
    return _require_collector().snapshot(process_limit=process_limit).to_dict()


def worker_stop() -> None:
    global _collector
    _collector = None


def _require_collector() -> LinuxSystemCollector:
    if _collector is None:
        raise RuntimeError("collector_not_started")
    return _collector


__all__ = [
    "BatteryMetrics",
    "CpuMetrics",
    "DiskMetrics",
    "LinuxSystemCollector",
    "MemoryMetrics",
    "ProcessMetrics",
    "SystemSnapshot",
    "ThermalSensor",
]
