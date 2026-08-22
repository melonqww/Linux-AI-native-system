"""Incremental metadata/content scheduler kept outside the interactive query path."""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Protocol
from uuid import uuid4

from ai_native_indexer import IndexerService
from ai_native_storage import CatalogEntry, EntryType, FileCatalog, PermissionLevel, VolumeRegistry

from .budget import ResourceBudget
from .contracts import EventKind, FileEvent, SchedulerState, SchedulerStatus
from .queue import CoalescingEventQueue


class _ExtractionResult(Protocol):
    @property
    def text(self) -> str: ...


class PdfExtractorLike(Protocol):
    def extract(self, path: Path) -> _ExtractionResult: ...


@dataclass
class _CrawlState:
    volume_id: str
    root: Path
    root_device: int
    scan_id: str
    directories: deque[Path]
    current: os.ScandirIterator[str] | None = None
    current_directory: Path | None = None
    protected_prefixes: list[str] = field(default_factory=list)


class IndexScheduler:
    def __init__(
        self,
        *,
        storage_database: Path,
        index_database: Path,
        queue: CoalescingEventQueue | None = None,
        budget: ResourceBudget | None = None,
        pdf_extractor: PdfExtractorLike | None = None,
    ) -> None:
        self.catalog = FileCatalog(storage_database)
        self.volumes = VolumeRegistry(storage_database)
        self.indexer = IndexerService(index_database)
        self.queue = queue or CoalescingEventQueue()
        self.budget = budget or ResourceBudget()
        self.pdf_extractor = pdf_extractor or self._load_pdf_extractor()
        self._processed = 0
        self._failed = 0
        self._state = SchedulerState.IDLE
        self._last_error: str | None = None
        self._crawls: dict[str, _CrawlState] = {}
        self._inaccessible = 0
        self._required_volumes: set[str] = set()
        self._completed_rescans: set[str] = set()

    def submit(self, event: FileEvent) -> None:
        self.queue.put(event)

    def request_rescan(self, volume_id: str) -> None:
        if volume_id != "*":
            self._required_volumes.add(volume_id)
            self._completed_rescans.discard(volume_id)
        self.submit(FileEvent(volume_id, "", EventKind.RESCAN, monotonic()))

    def process_once(self, *, now: float | None = None) -> SchedulerStatus:
        if self.budget.should_pause():
            self._state = SchedulerState.PAUSED_LOAD
            return self.status()
        events = self.queue.pop_ready(
            now=monotonic() if now is None else now,
            limit=self.budget.max_batch,
        )
        if not events and not self._crawls:
            self._state = SchedulerState.IDLE
            return self.status()
        self._state = SchedulerState.UPDATING
        for event in events:
            try:
                self._process(event)
                self._processed += 1
            except (OSError, RuntimeError, ValueError, PermissionError) as error:
                self._failed += 1
                self._last_error = f"{event.volume_id}:{event.path}: {error}"
                self._state = SchedulerState.DEGRADED
        remaining = max(0, self.budget.max_batch - len(events))
        if remaining:
            self._crawl_once(remaining)
        if self._state is SchedulerState.UPDATING and len(self.queue) == 0 and not self._crawls:
            self._state = SchedulerState.IDLE
        return self.status()

    def status(self) -> SchedulerStatus:
        return SchedulerStatus(
            state=self._state,
            queued=len(self.queue),
            processed=self._processed,
            failed=self._failed,
            last_error=self._last_error,
            active_rescans=len(self._crawls),
            inaccessible=self._inaccessible,
            coverage_complete=(
                bool(self._required_volumes)
                and self._required_volumes <= self._completed_rescans
                and not self._crawls
            ),
            covered_volume_ids=tuple(sorted(self._completed_rescans)),
            scanning_volume_ids=tuple(sorted(self._crawls)),
        )

    def record_failure(self, message: str) -> None:
        self._failed += 1
        self._last_error = message[:1_000]
        self._state = SchedulerState.DEGRADED

    def _process(self, event: FileEvent) -> None:
        if event.kind is EventKind.RESCAN:
            if event.volume_id == "*":
                for volume in self.volumes.list_volumes(available_only=True):
                    if volume.permission is not PermissionLevel.NONE:
                        self._start_crawl(volume.volume_id)
            else:
                self._start_crawl(event.volume_id)
            return
        path = Path(event.path)
        if event.kind is EventKind.DELETED:
            self.catalog.remove_path(event.volume_id, path)
            self.indexer.remove_path(path)
            return
        crawl = self._crawls.get(event.volume_id)
        entry = self.catalog.update_path(
            event.volume_id,
            path,
            scan_id=None if crawl is None else crawl.scan_id,
        )
        self._update_content(event.volume_id, path, entry)

    def _update_content(
        self, volume_id: str, path: Path, entry: CatalogEntry | None
    ) -> None:
        if entry is None or entry.entry_type is not EntryType.FILE or entry.sensitive:
            self.indexer.remove_path(path)
            return
        volume = self.volumes.get_volume(volume_id)
        if volume.permission is not PermissionLevel.CONTENT:
            self.indexer.remove_path(path)
            return
        root = Path(volume.mount_point)
        if entry.extension == ".pdf":
            if self.pdf_extractor is None:
                raise RuntimeError("documents.pdf optional dependency is unavailable")
            extracted = self.pdf_extractor.extract(path)
            self.indexer.index_text(path, extracted.text)
        else:
            self.indexer.index_file(path, root=root)

    @staticmethod
    def _load_pdf_extractor() -> PdfExtractorLike | None:
        try:
            from ai_native_pdf import PdfExtractor
        except ImportError:
            return None
        return PdfExtractor()

    def _start_crawl(self, volume_id: str) -> None:
        volume = self.volumes.get_volume(volume_id)
        if not volume.is_available or volume.permission is PermissionLevel.NONE:
            return
        previous = self._crawls.pop(volume_id, None)
        if previous is not None and previous.current is not None:
            previous.current.close()
        root = Path(volume.mount_point).resolve(strict=True)
        self._crawls[volume_id] = _CrawlState(
            volume_id=volume_id,
            root=root,
            root_device=root.stat().st_dev,
            scan_id=f"crawl:{uuid4()}",
            directories=deque([root]),
        )

    def _crawl_once(self, limit: int) -> None:
        processed = 0
        while processed < limit and self._crawls:
            volume_id = next(iter(self._crawls))
            crawl = self._crawls.pop(volume_id)
            self._crawls[volume_id] = crawl
            if crawl.current is None:
                if not crawl.directories:
                    removed = self.catalog.finish_incremental_scan(
                        volume_id,
                        crawl.scan_id,
                        protected_prefixes=crawl.protected_prefixes,
                    )
                    for removed_path in removed:
                        self.indexer.remove_path(Path(removed_path))
                    self._crawls.pop(volume_id)
                    self._completed_rescans.add(volume_id)
                    continue
                directory = crawl.directories.popleft()
                try:
                    crawl.current = os.scandir(directory)
                    crawl.current_directory = directory
                except OSError:
                    crawl.protected_prefixes.append(str(directory.absolute()))
                    self._inaccessible += 1
                    continue
            assert crawl.current is not None
            try:
                directory_entry = next(crawl.current)
            except StopIteration:
                crawl.current.close()
                crawl.current = None
                crawl.current_directory = None
                continue
            except OSError:
                crawl.current.close()
                crawl.current = None
                if crawl.current_directory is not None:
                    crawl.protected_prefixes.append(str(crawl.current_directory.absolute()))
                    self._inaccessible += 1
                crawl.current_directory = None
                continue
            path = Path(directory_entry.path)
            processed += 1
            try:
                stat = path.lstat()
                if stat.st_dev != crawl.root_device:
                    continue
                entry = self.catalog.update_path(volume_id, path, scan_id=crawl.scan_id)
                if entry is not None and entry.entry_type is EntryType.DIRECTORY:
                    crawl.directories.append(path)
                else:
                    self._update_content(volume_id, path, entry)
            except (OSError, PermissionError):
                crawl.protected_prefixes.append(str(path.absolute()))
                self._inaccessible += 1
            except (RuntimeError, ValueError) as error:
                self.record_failure(f"crawl {volume_id}:{path}: {error}")
