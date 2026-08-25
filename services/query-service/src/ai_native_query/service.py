"""One query surface over metadata, extracted content and collections."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable, Mapping

from ai_native_indexer import ContentIndexState, ContentIndexStatus, IndexerService
from ai_native_pdf import PdfExtractor
from ai_native_storage import (
    CatalogEntry,
    CollectionItem,
    EntryType,
    FileCatalog,
    FileQuery,
    PermissionLevel,
    StorageEnrollment,
)
from ai_native_storage.collections import VirtualCollectionStore
from ai_native_storage.registry import VolumeRegistry

from .contracts import (
    ContentAvailability,
    DocumentQuery,
    PdfIngestReport,
    QueryResult,
    SearchCoverage,
    SearchPage,
    SearchMode,
)


class QueryService:
    def __init__(
        self,
        *,
        storage_database: Path,
        index_database: Path,
        pdf_extractor: PdfExtractor | None = None,
        coverage_source: Callable[[], Mapping[str, object]] | None = None,
    ) -> None:
        self.catalog = FileCatalog(storage_database)
        self.volumes = VolumeRegistry(storage_database)
        self.enrollment = StorageEnrollment(self.volumes)
        self.collections = VirtualCollectionStore(storage_database)
        self.indexer = IndexerService(index_database)
        self.pdf_extractor = pdf_extractor or PdfExtractor()
        self.coverage_source = coverage_source

    def coverage(self, volume_ids: tuple[str, ...] = ()) -> SearchCoverage:
        catalog = self.catalog.status()
        available = self.volumes.list_volumes(available_only=True)
        selected = tuple(
            volume.volume_id
            for volume in available
            if volume.permission is not PermissionLevel.NONE
            and (not volume_ids or volume.volume_id in volume_ids)
        )
        status: Mapping[str, object] = {}
        if self.coverage_source is not None:
            try:
                status = self.coverage_source()
            except Exception:
                status = {}
        covered = self._status_volume_ids(status.get("covered_volume_ids"))
        scanning = self._status_volume_ids(status.get("scanning_volume_ids"))
        selected_set = set(selected)
        complete = (
            bool(selected_set)
            and selected_set <= set(covered)
            and not selected_set.intersection(scanning)
            if "covered_volume_ids" in status
            else bool(status.get("coverage_complete", False))
        )
        inaccessible = status.get("inaccessible", 0)
        inaccessible_count = (
            inaccessible if isinstance(inaccessible, int) and inaccessible >= 0 else 0
        )
        by_volume = catalog.get("by_volume", {})
        cataloged = (
            sum(
                int(by_volume.get(volume_id, 0))
                for volume_id in selected
            )
            if isinstance(by_volume, Mapping)
            else 0
        )
        warning = (
            "index_coverage_incomplete"
            if not complete
            else "index_items_inaccessible"
            if inaccessible_count
            else None
        )
        return SearchCoverage(
            complete=complete,
            state=("no_allowed_volumes" if not selected else str(status.get("state", "unknown"))),
            volume_ids=selected,
            cataloged_items=cataloged,
            inaccessible_items=inaccessible_count,
            warning=warning,
            covered_volume_ids=tuple(
                volume_id for volume_id in covered if volume_id in selected_set
            ),
            scanning_volume_ids=tuple(
                volume_id for volume_id in scanning if volume_id in selected_set
            ),
        )

    def ingest_pdfs(self, *, volume_ids: tuple[str, ...] = ()) -> PdfIngestReport:
        entries = self.catalog.search(
            FileQuery(extensions=("pdf",), volume_ids=volume_ids, limit=1_000)
        )
        indexed = 0
        unchanged = 0
        failures: list[str] = []
        for entry in entries:
            if self.volumes.get_volume(entry.volume_id).permission is not PermissionLevel.CONTENT:
                failures.append(f"{entry.path}: content permission is not granted")
                continue
            try:
                extracted = self.pdf_extractor.extract(Path(entry.path))
                changed = self.indexer.index_text(Path(entry.path), extracted.text)
            except (OSError, ValueError) as error:
                reason = getattr(error, "code", "pdf_extraction_failed")
                try:
                    self.indexer.record_unavailable(Path(entry.path), str(reason))
                except ValueError:
                    self.indexer.record_unavailable(
                        Path(entry.path), "pdf_extraction_failed"
                    )
                failures.append(f"{entry.path}: {error}")
                continue
            if changed:
                indexed += 1
            else:
                unchanged += 1
        return PdfIngestReport(
            discovered=len(entries),
            indexed=indexed,
            unchanged=unchanged,
            failed=len(failures),
            failures=tuple(failures),
        )

    def search(self, query: DocumentQuery) -> list[QueryResult]:
        return list(self.search_page(query).results)

    def search_page(self, query: DocumentQuery) -> SearchPage:
        if not 1 <= query.limit <= 100:
            raise ValueError("query limit must be from 1 to 100")
        if (
            not isinstance(query.offset, int)
            or isinstance(query.offset, bool)
            or query.offset < 0
        ):
            raise ValueError("query offset must be a non-negative integer")
        mode = query.mode
        if mode is None:
            mode = (
                SearchMode.HYBRID
                if query.text.strip() and (query.name_contains or query.extensions)
                else SearchMode.CONTENT
                if query.text.strip()
                else SearchMode.METADATA
            )
        if not isinstance(mode, SearchMode):
            raise ValueError("query mode must use SearchMode")
        if mode is SearchMode.METADATA and query.text.strip():
            raise ValueError("metadata search cannot contain a content query")
        if mode in {SearchMode.CONTENT, SearchMode.HYBRID} and not query.text.strip():
            raise ValueError("content and hybrid search require text")
        metadata_query = FileQuery(
            name_contains=query.name_contains,
            extensions=query.extensions,
            volume_ids=query.volume_ids,
            limit=query.limit,
            offset=query.offset,
        )
        coverage = self.coverage(query.volume_ids)
        if mode is SearchMode.METADATA:
            metadata_entries = self.catalog.search(metadata_query)
            index_states = self.indexer.content_statuses(
                [entry.path for entry in metadata_entries]
            )
            results = []
            for entry in metadata_entries:
                availability, reason = self._content_availability(entry, index_states)
                results.append(
                    QueryResult(
                        volume_id=entry.volume_id,
                        path=entry.path,
                        name=entry.name,
                        extension=entry.extension,
                        score=0.2,
                        snippet=None,
                        line_start=None,
                        line_end=None,
                        sources=("metadata",),
                        size_bytes=entry.size_bytes,
                        mtime_ns=entry.mtime_ns,
                        content_state=availability,
                        content_reason=reason,
                    )
                )
            return SearchPage(
                tuple(results),
                query.offset,
                query.limit,
                self.catalog.count(metadata_query),
                True,
                coverage,
            )

        candidate_limit = 10_001
        content_hits = self.indexer.search_candidates(query.text, limit=candidate_limit)
        catalog_for_hits = self.catalog.entries_by_paths([hit.path for hit in content_hits])
        maximum_content_score = max((hit.score for hit in content_hits), default=1.0) or 1.0
        results: list[QueryResult] = []
        content_allowed: dict[str, bool] = {}
        for hit in content_hits:
            entry = catalog_for_hits.get(hit.path)
            if entry is None:
                continue
            if entry.volume_id not in content_allowed:
                try:
                    content_allowed[entry.volume_id] = (
                        self.volumes.get_volume(entry.volume_id).permission
                        is PermissionLevel.CONTENT
                    )
                except KeyError:
                    content_allowed[entry.volume_id] = False
            if not content_allowed[entry.volume_id]:
                continue
            if query.extensions and entry.extension not in {
                extension.casefold() if extension.startswith(".") else f".{extension.casefold()}"
                for extension in query.extensions
            }:
                continue
            if query.volume_ids and entry.volume_id not in query.volume_ids:
                continue
            if query.name_contains and not all(
                term.casefold() in entry.name.casefold() for term in query.name_contains
            ):
                continue
            content_score = 0.5 + 0.5 * (hit.score / maximum_content_score)
            results.append(
                QueryResult(
                    volume_id=entry.volume_id,
                    path=entry.path,
                    name=entry.name,
                    extension=entry.extension,
                    score=round(content_score, 6),
                    snippet=hit.content,
                    line_start=hit.line_start,
                    line_end=hit.line_end,
                    sources=("content",),
                    size_bytes=entry.size_bytes,
                    mtime_ns=entry.mtime_ns,
                    content_state=ContentAvailability.INDEXED,
                    content_reason=None,
                )
            )
        results.sort(key=lambda item: (-item.score, item.name.casefold(), item.path))
        total = len(results)
        return SearchPage(
            tuple(results[query.offset : query.offset + query.limit]),
            query.offset,
            query.limit,
            total,
            len(content_hits) < candidate_limit,
            coverage,
        )

    def save_snapshot(self, title: str, results: list[QueryResult]) -> str:
        entries = self.catalog.entries_by_paths([result.path for result in results])
        items = [
            CollectionItem(
                volume_id=result.volume_id,
                path=result.path,
                stable_key=entries[result.path].stable_key,
                name=result.name,
                score=result.score,
                snippet=result.snippet,
            )
            for result in results
            if result.path in entries
        ]
        return self.collections.create_snapshot(title, items).collection_id

    def _content_availability(
        self, entry: CatalogEntry, statuses: Mapping[str, ContentIndexStatus]
    ) -> tuple[ContentAvailability, str | None]:
        volume = self.volumes.get_volume(entry.volume_id)
        if volume.permission is not PermissionLevel.CONTENT:
            return ContentAvailability.NOT_PERMITTED, "content_permission_required"
        if entry.entry_type is not EntryType.FILE:
            return ContentAvailability.UNSUPPORTED, "not_a_regular_file"
        status = statuses.get(entry.path)
        if status is not None:
            state = status.state
            reason = status.reason
            if state is ContentIndexState.INDEXED:
                return ContentAvailability.INDEXED, None
            if state is ContentIndexState.UNSUPPORTED:
                return ContentAvailability.UNSUPPORTED, reason
            return ContentAvailability.UNAVAILABLE, reason
        extension = entry.extension
        supported = extension == ".pdf" or extension in self.indexer.file_policy.extensions
        if not supported:
            return ContentAvailability.UNSUPPORTED, "unsupported_type"
        return ContentAvailability.PENDING, "not_indexed_yet"

    @staticmethod
    def _status_volume_ids(value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(
            dict.fromkeys(item for item in value if isinstance(item, str) and item)
        )
