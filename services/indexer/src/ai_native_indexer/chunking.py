"""Small deterministic chunks which preserve source line numbers."""

from bisect import bisect_right
from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    ordinal: int
    line_start: int
    line_end: int
    content: str


def chunk_text(text: str, *, target_chars: int = 1_200, overlap_chars: int = 200) -> list[TextChunk]:
    if target_chars < 1 or overlap_chars < 0 or overlap_chars >= target_chars:
        raise ValueError("chunk sizes must satisfy 0 <= overlap_chars < target_chars")
    if not text.strip():
        return []

    newline_positions = [position for position, character in enumerate(text) if character == "\n"]
    chunks: list[TextChunk] = []
    start = 0

    while start < len(text):
        end = min(start + target_chars, len(text))
        if end < len(text):
            newline = text.rfind("\n", start + target_chars // 2, end)
            if newline != -1:
                end = newline + 1

        raw = text[start:end]
        left_trimmed = len(raw) - len(raw.lstrip())
        content = raw.strip()
        if content:
            content_start = start + left_trimmed
            content_end = content_start + len(content) - 1
            chunks.append(
                TextChunk(
                    ordinal=len(chunks),
                    line_start=bisect_right(newline_positions, content_start) + 1,
                    line_end=bisect_right(newline_positions, content_end) + 1,
                    content=content,
                )
            )

        if end >= len(text):
            break
        next_start = max(start + 1, end - overlap_chars)
        previous_newline = text.rfind("\n", start + 1, next_start)
        if previous_newline != -1:
            next_start = previous_newline + 1
        else:
            next_newline = text.find("\n", next_start, end)
            if next_newline != -1:
                next_start = next_newline + 1
            else:
                next_space = text.find(" ", next_start, end)
                if next_space != -1:
                    next_start = next_space + 1
        start = next_start

    return chunks
