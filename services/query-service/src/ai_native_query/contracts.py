from dataclasses import dataclass
from enum import StrEnum


class SearchMode(StrEnum):
    METADATA = "metadata"
    CONTENT = "content"
    HYBRID = "hybrid"


class ContentAvailability(StrEnum):
    INDEXED = "indexed"
    PENDING = "pending"
    UNAVAILABLE = "unavailable"
    NOT_PERMITTED = "not_permitted"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class DocumentQuery:
    mode: SearchMode | None = None
    text: str = ""
    name_contains: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    volume_ids: tuple[str, ...] = ()
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True)
class QueryResult:
    volume_id: str
    path: str
    name: str
    extension: str
    score: float
    snippet: str | None
    line_start: int | None
    line_end: int | None
    sources: tuple[str, ...]
    size_bytes: int = 0
    mtime_ns: int = 0
    content_state: ContentAvailability = ContentAvailability.PENDING
    content_reason: str | None = None


@dataclass(frozen=True)
class SearchCoverage:
    complete: bool
    state: str
    volume_ids: tuple[str, ...]
    cataloged_items: int
    inaccessible_items: int = 0
    warning: str | None = None
    covered_volume_ids: tuple[str, ...] = ()
    scanning_volume_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchPage:
    results: tuple[QueryResult, ...]
    offset: int
    limit: int
    total_matches: int
    total_is_exact: bool
    coverage: SearchCoverage


@dataclass(frozen=True)
class PdfIngestReport:
    discovered: int
    indexed: int
    unchanged: int
    failed: int
    failures: tuple[str, ...]
