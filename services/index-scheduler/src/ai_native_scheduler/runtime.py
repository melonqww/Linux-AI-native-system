"""Lifecycle hooks used by the isolated module worker."""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from .inotify import LinuxInotifyWatcher
from .scheduler import IndexScheduler
from .service import BackgroundIndexService


class _NullWatcher:
    def add_tree(self, _volume_id: str, _root: Path) -> int:
        return 0

    def read(self, *, timeout: float = 0.0):
        if timeout > 0:
            time.sleep(timeout)
        return []

    def close(self) -> None:
        return

    def remove_volume(self, _volume_id: str) -> None:
        return


class SchedulerRuntime:
    def __init__(self, storage_database: Path, index_database: Path) -> None:
        self.scheduler = IndexScheduler(
            storage_database=storage_database,
            index_database=index_database,
        )
        self.watcher = LinuxInotifyWatcher() if sys.platform.startswith("linux") else _NullWatcher()
        self.service = BackgroundIndexService(self.scheduler, self.watcher)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="storage-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.watcher.close()
        self._thread = None

    def health(self) -> dict[str, object]:
        status = asdict(self.scheduler.status())
        status["thread_alive"] = self._thread is not None and self._thread.is_alive()
        return status

    def _run(self) -> None:
        try:
            self.service.start()
            while not self._stop.is_set():
                self.service.tick(watch_timeout=0.5)
                self._stop.wait(0.1)
        except Exception as error:
            self.scheduler.record_failure(f"background runtime: {type(error).__name__}: {error}")


_runtime: SchedulerRuntime | None = None


def worker_start() -> None:
    global _runtime
    if _runtime is not None:
        return
    storage = Path(os.environ["AI_NATIVE_STORAGE_DATABASE"])
    index = Path(os.environ["AI_NATIVE_INDEX_DATABASE"])
    _runtime = SchedulerRuntime(storage, index)
    _runtime.start()


def worker_health() -> dict[str, object]:
    return {"state": "not_started"} if _runtime is None else _runtime.health()


def worker_stop() -> None:
    global _runtime
    if _runtime is not None:
        _runtime.stop()
        _runtime = None
