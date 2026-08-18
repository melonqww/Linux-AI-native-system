"""One query surface over metadata, extracted content and collections."""

from __future__ import annotations

from pathlib import Path

from ai_native_indexer import IndexerService
from ai_native_pdf import PdfExtractionError, PdfExtractor
from ai_native_storage import CollectionItem, FileCatalog, FileQuery, PermissionLevel
from ai_native_storage.collections import VirtualCollectionStore
from ai_native_storage.registry import VolumeRegistry

from .contracts import DocumentQuery, PdfIngestReport, QueryResult


class QueryService:
    def __init__(
        self,
        *,
        storage_database: Path,
        index_database: Path,
        pdf_extractor: PdfExtractor | None = None,
    ) -> None:
        self.catalog = FileCatalog(storage_database)
        self.volumes = VolumeRegistry(storage_database)
        self.collections = VirtualCollectionStore(storage_database)
        self.indexer = IndexerService(index_database)
        self.pdf_extractor = pdf_extractor or PdfExtractor()

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
            except (OSError, ValueError, PdfExtractionError) as error:
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
        if not 1 <= query.limit <= 100:
            raise ValueError("query limit must be from 1 to 100")
        metadata_entries = self.catalog.search(
            FileQuery(
                name_contains=query.name_contains,
                extensions=query.extensions,
                volume_ids=query.volume_ids,
                limit=min(1_000, max(100, query.limit * 10)),
            )
        )
        metadata_by_path = {entry.path: entry for entry in metadata_entries}
        content_hits = self.indexer.search(query.text, limit=50) if query.text.strip() else []
        catalog_for_hits = self.catalog.entries_by_paths([hit.path for hit in content_hits])
        maximum_content_score = max((hit.score for hit in content_hits), default=1.0) or 1.0
        merged: dict[str, QueryResult] = {}

        # Extension and volume are filters, not relevance signals.  When content text
        # is present, do not return every file of the requested type as a weak hit.
        if not query.text.strip() or query.name_contains:
            for entry in metadata_entries:
                merged[entry.path] = QueryResult(
                    volume_id=entry.volume_id,
                    path=entry.path,
                    name=entry.name,
                    extension=entry.extension,
                    score=0.2,
                    snippet=None,
                    line_start=None,
                    line_end=None,
                    sources=("metadata",),
                )
        for hit in content_hits:
            entry = catalog_for_hits.get(hit.path)
            if entry is None:
                continue
            if query.extensions and entry.extension not in {
                extension.casefold() if extension.startswith(".") else f".{extension.casefold()}"
                for extension in query.extensions
            }:
                continue
            if query.volume_ids and entry.volume_id not in query.volume_ids:
                continue
            content_score = 0.5 + 0.5 * (hit.score / maximum_content_score)
            existing = merged.get(hit.path)
            sources = ("content",) if existing is None else ("metadata", "content")
            merged[hit.path] = QueryResult(
                volume_id=entry.volume_id,
                path=entry.path,
                name=entry.name,
                extension=entry.extension,
                score=round(content_score + (0.1 if existing else 0), 6),
                snippet=hit.content,
                line_start=hit.line_start,
                line_end=hit.line_end,
                sources=sources,
            )
        return sorted(merged.values(), key=lambda item: (-item.score, item.name.casefold()))[
            : query.limit
        ]

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
