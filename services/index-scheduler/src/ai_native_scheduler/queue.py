"""Bounded, thread-safe filesystem event coalescing."""

from __future__ import annotations

import threading
from collections import OrderedDict

from .contracts import EventKind, FileEvent


class CoalescingEventQueue:
    def __init__(self, *, max_events: int = 10_000, debounce_seconds: float = 0.25) -> None:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        if debounce_seconds < 0:
            raise ValueError("debounce_seconds must not be negative")
        self.max_events = max_events
        self.debounce_seconds = debounce_seconds
        self._events: OrderedDict[tuple[str, str], FileEvent] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, event: FileEvent) -> None:
        if not event.volume_id.strip():
            raise ValueError("volume_id must not be empty")
        if event.kind is not EventKind.RESCAN and not event.path:
            raise ValueError("non-rescan event path must not be empty")
        key = (event.volume_id, "" if event.kind is EventKind.RESCAN else event.path)
        with self._lock:
            if ("*", "") in self._events:
                return
            rescan_key = (event.volume_id, "")
            if rescan_key in self._events and event.kind is not EventKind.RESCAN:
                return
            previous = self._events.pop(key, None)
            if previous is not None:
                event = self._merge(previous, event)
            elif len(self._events) >= self.max_events:
                self._events.clear()
                self._events[("*", "")] = FileEvent(
                    volume_id="*",
                    path="",
                    kind=EventKind.RESCAN,
                    observed_at=event.observed_at,
                )
                return
            if event.kind is EventKind.RESCAN:
                self._collapse_volume(event.volume_id, event.observed_at)
            else:
                self._events[key] = event

    def pop_ready(self, *, now: float, limit: int) -> list[FileEvent]:
        if limit < 1:
            raise ValueError("limit must be positive")
        ready: list[FileEvent] = []
        with self._lock:
            for key, event in list(self._events.items()):
                if len(ready) >= limit:
                    break
                if event.kind is not EventKind.RESCAN and now - event.observed_at < self.debounce_seconds:
                    continue
                ready.append(self._events.pop(key))
        return ready

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def _collapse_volume(self, volume_id: str, observed_at: float) -> None:
        for key in [key for key in self._events if key[0] == volume_id]:
            self._events.pop(key)
        self._events[(volume_id, "")] = FileEvent(
            volume_id=volume_id,
            path="",
            kind=EventKind.RESCAN,
            observed_at=observed_at,
        )

    @staticmethod
    def _merge(previous: FileEvent, current: FileEvent) -> FileEvent:
        if current.kind is EventKind.DELETED:
            kind = EventKind.DELETED
        elif previous.kind is EventKind.DELETED and current.kind is EventKind.CREATED:
            kind = EventKind.MODIFIED
        elif previous.kind is EventKind.CREATED and current.kind is EventKind.MODIFIED:
            kind = EventKind.CREATED
        else:
            kind = current.kind
        return FileEvent(current.volume_id, current.path, kind, current.observed_at)
