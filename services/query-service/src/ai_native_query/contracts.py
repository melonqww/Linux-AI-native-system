from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentQuery:
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
class PdfIngestReport:
    discovered: int
    indexed: int
    unchanged: int
    failed: int
    failures: tuple[str, ...]
