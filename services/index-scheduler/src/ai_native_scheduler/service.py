"""Coordinator for mount polling, filesystem events and bounded index work."""

from __future__ import annotations

from pathlib import Path
from time import monotonic
from typing import Protocol

from ai_native_storage import PermissionLevel

from .contracts import FileEvent, SchedulerStatus
from .mounts import VolumeMonitor
from .scheduler import IndexScheduler
from .inotify import WatchLimitError


class Watcher(Protocol):
    def add_tree(self, volume_id: str, root: Path) -> int: ...
    def read(self, *, timeout: float = 0.0) -> list[FileEvent]: ...
    def remove_volume(self, volume_id: str) -> None: ...


class BackgroundIndexService:
    def __init__(
        self,
        scheduler: IndexScheduler,
        watcher: Watcher,
        *,
        mount_poll_seconds: float = 10.0,
        fallback_rescan_seconds: float = 900.0,
    ) -> None:
        if mount_poll_seconds <= 0:
            raise ValueError("mount_poll_seconds must be positive")
        if fallback_rescan_seconds <= 0:
            raise ValueError("fallback_rescan_seconds must be positive")
        self.scheduler = scheduler
        self.watcher = watcher
        self.monitor = VolumeMonitor(scheduler.volumes)
        self.mount_poll_seconds = mount_poll_seconds
        self.fallback_rescan_seconds = fallback_rescan_seconds
        self._next_mount_poll = 0.0
        self._watched_volumes: set[str] = set()
        self._fallback_rescans: dict[str, float] = {}

    def start(self) -> None:
        for volume in self.scheduler.volumes.list_volumes(available_only=True):
            if volume.permission is not PermissionLevel.NONE:
                self._watch(volume.volume_id, Path(volume.mount_point))
                # A watcher only observes future changes. Always reconcile the existing
                # tree after startup so a persisted but incomplete catalog cannot look
                # like a complete empty disk.
                self.scheduler.resume_or_request_rescan(volume.volume_id)

    def tick(self, *, now: float | None = None, watch_timeout: float = 0.0) -> SchedulerStatus:
        current = monotonic() if now is None else now
        for event in self.watcher.read(timeout=watch_timeout):
            self.scheduler.submit(event)
        if current >= self._next_mount_poll:
            change = self.monitor.poll()
            for volume_id in change.disconnected:
                self._watched_volumes.discard(volume_id)
                self._fallback_rescans.pop(volume_id, None)
            for volume_id in change.connected:
                volume = self.scheduler.volumes.get_volume(volume_id)
                if volume.permission is not PermissionLevel.NONE:
                    self._watch(volume_id, Path(volume.mount_point))
                    self.scheduler.request_rescan(volume_id)
            permitted = {
                volume.volume_id: volume
                for volume in self.scheduler.volumes.list_volumes(available_only=True)
                if volume.permission is not PermissionLevel.NONE
            }
            for volume_id in self._watched_volumes - permitted.keys():
                self.watcher.remove_volume(volume_id)
                self._watched_volumes.discard(volume_id)
                self._fallback_rescans.pop(volume_id, None)
            for volume_id, volume in permitted.items():
                if volume_id not in self._watched_volumes:
                    self._watch(volume_id, Path(volume.mount_point))
                    self.scheduler.request_rescan(volume_id)
            self._next_mount_poll = current + self.mount_poll_seconds
        for volume_id, scheduled_at in list(self._fallback_rescans.items()):
            if current >= scheduled_at:
                self.scheduler.request_rescan(volume_id)
                self._fallback_rescans[volume_id] = current + self.fallback_rescan_seconds
        return self.scheduler.process_once(now=current)

    def _watch(self, volume_id: str, mount_point: Path) -> None:
        if volume_id in self._watched_volumes:
            return
        try:
            self.watcher.add_tree(volume_id, mount_point)
        except (OSError, ValueError, WatchLimitError) as error:
            self.scheduler.record_failure(f"watch {volume_id}: {error}")
            self.scheduler.request_rescan(volume_id)
            self._fallback_rescans[volume_id] = monotonic() + self.fallback_rescan_seconds
        self._watched_volumes.add(volume_id)
