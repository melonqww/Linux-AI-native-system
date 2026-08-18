"""Dependency-free Linux inotify adapter with a pure decoder for cross-platform tests."""

from __future__ import annotations

import ctypes
import os
import selectors
import struct
import sys
from pathlib import Path
from time import monotonic

from .contracts import EventKind, FileEvent


IN_ACCESS = 0x00000001
IN_MODIFY = 0x00000002
IN_ATTRIB = 0x00000004
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_Q_OVERFLOW = 0x00004000
IN_IGNORED = 0x00008000
IN_ONLYDIR = 0x01000000
IN_DONT_FOLLOW = 0x02000000
IN_EXCL_UNLINK = 0x04000000
IN_ISDIR = 0x40000000

WATCH_MASK = (
    IN_MODIFY
    | IN_ATTRIB
    | IN_CLOSE_WRITE
    | IN_MOVED_FROM
    | IN_MOVED_TO
    | IN_CREATE
    | IN_DELETE
    | IN_DELETE_SELF
    | IN_MOVE_SELF
    | IN_EXCL_UNLINK
    | IN_DONT_FOLLOW
)
_HEADER = struct.Struct("iIII")


class WatchLimitError(RuntimeError):
    pass


def decode_inotify_events(
    payload: bytes,
    watches: dict[int, tuple[str, Path]],
    *,
    observed_at: float,
) -> list[tuple[FileEvent, bool]]:
    decoded: list[tuple[FileEvent, bool]] = []
    offset = 0
    while offset + _HEADER.size <= len(payload):
        watch_descriptor, mask, _cookie, name_length = _HEADER.unpack_from(payload, offset)
        offset += _HEADER.size
        end = offset + name_length
        if end > len(payload):
            break
        raw_name = payload[offset:end].split(b"\0", 1)[0]
        offset = end
        if mask & IN_Q_OVERFLOW:
            for volume_id in sorted({volume for volume, _path in watches.values()}):
                decoded.append(
                    (FileEvent(volume_id, "", EventKind.RESCAN, observed_at), False)
                )
            continue
        watched = watches.get(watch_descriptor)
        if watched is None or mask & IN_IGNORED:
            continue
        volume_id, directory = watched
        name = raw_name.decode("utf-8", errors="surrogateescape")
        if name in {".", ".."} or (name and Path(name).name != name):
            continue
        path = directory / name if name else directory
        if mask & (IN_DELETE | IN_DELETE_SELF | IN_MOVED_FROM | IN_MOVE_SELF):
            kind = EventKind.DELETED
        elif mask & (IN_CREATE | IN_MOVED_TO):
            kind = EventKind.CREATED
        elif mask & (IN_MODIFY | IN_ATTRIB | IN_CLOSE_WRITE):
            kind = EventKind.MODIFIED
        else:
            continue
        decoded.append(
            (FileEvent(volume_id, str(path), kind, observed_at), bool(mask & IN_ISDIR))
        )
    return decoded


class LinuxInotifyWatcher:
    def __init__(self, *, max_watches: int = 8_192) -> None:
        if not sys.platform.startswith("linux"):
            raise OSError("inotify is available only on Linux")
        if max_watches < 1:
            raise ValueError("max_watches must be positive")
        self.max_watches = max_watches
        self._libc = ctypes.CDLL(None, use_errno=True)
        self._libc.inotify_init1.argtypes = [ctypes.c_int]
        self._libc.inotify_init1.restype = ctypes.c_int
        self._libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        self._libc.inotify_add_watch.restype = ctypes.c_int
        self._libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
        self._libc.inotify_rm_watch.restype = ctypes.c_int
        self._fd = self._libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self._fd < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        self._watches: dict[int, tuple[str, Path]] = {}
        self._paths: set[tuple[str, str]] = set()

    def add_tree(self, volume_id: str, root: Path) -> int:
        root = root.resolve(strict=True)
        if not root.is_dir() or root.is_symlink():
            raise ValueError("watch root must be a normal directory")
        root_device = root.stat().st_dev
        added = 0
        for current, directory_names, _file_names in os.walk(root, followlinks=False):
            current_path = Path(current)
            kept: list[str] = []
            for name in directory_names:
                child = current_path / name
                try:
                    if not child.is_symlink() and child.stat().st_dev == root_device:
                        kept.append(name)
                except OSError:
                    continue
            directory_names[:] = kept
            if self._add_watch(volume_id, current_path):
                added += 1
        return added

    def read(self, *, timeout: float = 0.0) -> list[FileEvent]:
        if self._fd < 0:
            return []
        selector = selectors.DefaultSelector()
        try:
            selector.register(self._fd, selectors.EVENT_READ)
            if not selector.select(timeout):
                return []
        finally:
            selector.close()
        try:
            payload = os.read(self._fd, 256 * 1024)
        except BlockingIOError:
            return []
        decoded = decode_inotify_events(payload, self._watches, observed_at=monotonic())
        events: list[FileEvent] = []
        for event, is_directory in decoded:
            events.append(event)
            if is_directory and event.kind is EventKind.CREATED:
                path = Path(event.path)
                try:
                    if path.is_dir() and not path.is_symlink():
                        self.add_tree(event.volume_id, path)
                except (OSError, ValueError, WatchLimitError):
                    events.append(
                        FileEvent(event.volume_id, "", EventKind.RESCAN, event.observed_at)
                    )
        return events

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1
        self._watches.clear()
        self._paths.clear()

    def remove_volume(self, volume_id: str) -> None:
        for descriptor, watched in list(self._watches.items()):
            watched_volume, path = watched
            if watched_volume != volume_id:
                continue
            self._libc.inotify_rm_watch(self._fd, descriptor)
            self._watches.pop(descriptor, None)
            self._paths.discard((volume_id, str(path)))

    def _add_watch(self, volume_id: str, path: Path) -> bool:
        key = (volume_id, str(path))
        if key in self._paths:
            return False
        if len(self._watches) >= self.max_watches:
            raise WatchLimitError(f"inotify watch budget exceeded: {self.max_watches}")
        descriptor = self._libc.inotify_add_watch(
            self._fd,
            os.fsencode(path),
            WATCH_MASK | IN_ONLYDIR,
        )
        if descriptor < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(path))
        self._watches[descriptor] = (volume_id, path)
        self._paths.add(key)
        return True

    def __enter__(self) -> "LinuxInotifyWatcher":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
