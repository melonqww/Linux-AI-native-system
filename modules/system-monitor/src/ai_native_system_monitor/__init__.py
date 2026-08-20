"""First-party system.monitor worker entrypoint."""

from __future__ import annotations

from .collector import LinuxSystemCollector
from .contracts import (
    BatteryMetrics,
    CpuMetrics,
    DiskMetrics,
    MemoryModule,
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
    if set(payload) - {"process_limit", "process_sort", "process_order"}:
        raise ValueError("invalid_payload")
    process_limit = payload.get("process_limit", 20)
    process_sort = payload.get("process_sort", "cpu")
    process_order = payload.get("process_order", "desc")
    return _require_collector().snapshot(
        process_limit=process_limit,
        process_sort=process_sort,
        process_order=process_order,
    ).to_dict()


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
    "MemoryModule",
    "ProcessMetrics",
    "SystemSnapshot",
    "ThermalSensor",
]
