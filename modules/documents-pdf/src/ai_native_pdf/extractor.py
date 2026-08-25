"""Bounded, page-aware PDF text extraction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pypdf import PdfReader


class PdfFailureCode(StrEnum):
    INVALID_SOURCE = "invalid_source"
    UNREADABLE = "unreadable"
    TOO_LARGE = "too_large"
    DAMAGED = "damaged"
    ENCRYPTED = "encrypted"
    TOO_MANY_PAGES = "too_many_pages"
    PAGE_EXTRACTION_FAILED = "page_extraction_failed"
    NEEDS_OCR = "needs_ocr"


class PdfExtractionError(ValueError):
    """Bounded extraction failure with a stable machine-readable reason."""

    def __init__(self, code: PdfFailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code


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
            raise PdfExtractionError(
                PdfFailureCode.INVALID_SOURCE,
                "source must be a regular non-symlink file",
            )
        try:
            path = path.resolve(strict=True)
        except OSError as error:
            raise PdfExtractionError(
                PdfFailureCode.UNREADABLE, "PDF source is unavailable"
            ) from error
        if path.suffix.casefold() != ".pdf":
            raise PdfExtractionError(PdfFailureCode.INVALID_SOURCE, "source must be a PDF file")
        if not path.is_file():
            raise PdfExtractionError(
                PdfFailureCode.INVALID_SOURCE,
                "source must be a regular non-symlink file",
            )
        try:
            size = path.stat().st_size
        except OSError as error:
            raise PdfExtractionError(
                PdfFailureCode.UNREADABLE, "PDF metadata is unavailable"
            ) from error
        if size > self.max_bytes:
            raise PdfExtractionError(
                PdfFailureCode.TOO_LARGE, "PDF exceeds the configured size limit"
            )
        try:
            reader = PdfReader(path, strict=False)
        except Exception as error:
            raise PdfExtractionError(
                PdfFailureCode.DAMAGED,
                f"cannot open PDF: {type(error).__name__}",
            ) from error
        if reader.is_encrypted:
            raise PdfExtractionError(
                PdfFailureCode.ENCRYPTED,
                "encrypted PDF requires a separate approval flow",
            )
        if len(reader.pages) > self.max_pages:
            raise PdfExtractionError(
                PdfFailureCode.TOO_MANY_PAGES,
                "PDF exceeds the configured page limit",
            )

        pages: list[PdfPage] = []
        for number, page in enumerate(reader.pages, start=1):
            try:
                text = (page.extract_text() or "").strip()
            except Exception as error:
                raise PdfExtractionError(
                    PdfFailureCode.PAGE_EXTRACTION_FAILED,
                    f"cannot extract PDF page {number}",
                ) from error
            pages.append(PdfPage(page_number=number, text=text))
        if not any(page.text for page in pages):
            raise PdfExtractionError(
                PdfFailureCode.NEEDS_OCR,
                "PDF contains no extractable text; OCR module is required",
            )
        return PdfExtractionResult(path=str(path), pages=tuple(pages), page_count=len(reader.pages))
