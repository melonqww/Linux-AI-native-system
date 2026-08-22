from dataclasses import dataclass
from enum import StrEnum


class SearchMode(StrEnum):
    METADATA = "metadata"
    CONTENT = "content"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class DocumentQuery:
    mode: SearchMode | None = None
    text: str = ""
    name_contains: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    volume_ids: tuple[str, ...] = ()
    limit: int = 20


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


@dataclass(frozen=True)
class SearchCoverage:
    complete: bool
    state: str
    volume_ids: tuple[str, ...]
    cataloged_items: int
    inaccessible_items: int = 0
    warning: str | None = None


@dataclass(frozen=True)
class PdfIngestReport:
    discovered: int
    indexed: int
    unchanged: int
    failed: int
    failures: tuple[str, ...]
