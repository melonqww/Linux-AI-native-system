"""Bounded, page-aware PDF text extraction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


class PdfExtractionError(ValueError):
    pass


@dataclass(frozen=True)
class PdfPage:
    page_number: int
    text: str


@dataclass(frozen=True)
class PdfExtractionResult:
    path: str
    pages: tuple[PdfPage, ...]
    page_count: int

    @property
    def text(self) -> str:
        return "\n\n".join(
            f"[PDF page {page.page_number}]\n{page.text}" for page in self.pages if page.text
        )


class PdfExtractor:
    def __init__(self, *, max_bytes: int = 100 * 1_048_576, max_pages: int = 2_000) -> None:
        self.max_bytes = max_bytes
        self.max_pages = max_pages

    def extract(self, path: Path) -> PdfExtractionResult:
        path = path.expanduser()
        if path.is_symlink():
            raise PdfExtractionError("source must be a regular non-symlink file")
        path = path.resolve(strict=True)
        if path.suffix.casefold() != ".pdf":
            raise PdfExtractionError("source must be a PDF file")
        if not path.is_file():
            raise PdfExtractionError("source must be a regular non-symlink file")
        if path.stat().st_size > self.max_bytes:
            raise PdfExtractionError("PDF exceeds the configured size limit")
        try:
            reader = PdfReader(path, strict=False)
        except Exception as error:
            raise PdfExtractionError(f"cannot open PDF: {type(error).__name__}") from error
        if reader.is_encrypted:
            raise PdfExtractionError("encrypted PDF requires a separate approval flow")
        if len(reader.pages) > self.max_pages:
            raise PdfExtractionError("PDF exceeds the configured page limit")

        pages: list[PdfPage] = []
        for number, page in enumerate(reader.pages, start=1):
            try:
                text = (page.extract_text() or "").strip()
            except Exception as error:
                raise PdfExtractionError(f"cannot extract PDF page {number}") from error
            pages.append(PdfPage(page_number=number, text=text))
        if not any(page.text for page in pages):
            raise PdfExtractionError("PDF contains no extractable text; OCR module is required")
        return PdfExtractionResult(path=str(path), pages=tuple(pages), page_count=len(reader.pages))
