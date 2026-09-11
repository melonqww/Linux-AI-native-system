"""Optional multilingual retrieval over an already authorized local corpus."""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Sequence
from threading import RLock
from time import monotonic
from typing import Protocol

from ai_native_indexer import SearchHit


class EmbeddingProvider(Protocol):
    """Small provider contract; production and tests can supply different adapters."""

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


class SemanticContentRetriever:
    """Ranks allowed chunks and becomes an empty result on provider failure."""

    def __init__(
        self,
        provider: EmbeddingProvider,
        *,
        minimum_score: float = 0.5,
        batch_size: int = 32,
        vector_cache_size: int = 2_048,
        unavailable_retry_seconds: float = 60.0,
    ) -> None:
        if not 0 <= minimum_score <= 1:
            raise ValueError("semantic minimum_score must be from 0 to 1")
        if not 1 <= batch_size <= 64:
            raise ValueError("semantic batch size must be from 1 to 64")
        if not 1 <= vector_cache_size <= 8_192:
            raise ValueError("semantic vector cache size must be from 1 to 8192")
        if not 1 <= unavailable_retry_seconds <= 600:
            raise ValueError("semantic retry delay must be from 1 to 600 seconds")
        self.provider = provider
        self.minimum_score = float(minimum_score)
        self.batch_size = batch_size
        self.vector_cache_size = vector_cache_size
        self.unavailable_retry_seconds = float(unavailable_retry_seconds)
        self._vectors: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._retry_after = 0.0
        self._lock = RLock()

    def search(
        self, query: str, corpus: Sequence[SearchHit], *, limit: int
    ) -> list[SearchHit]:
        if not corpus or not query.strip():
            return []
        with self._lock:
            if monotonic() < self._retry_after:
                return []
        try:
            with self._lock:
                query_vectors = self.provider.embed((self._query_text(query),))
                if len(query_vectors) != 1:
                    return []
                query_vector = query_vectors[0]
                vectors = self._document_vectors(corpus)
        except Exception:
            # Semantic retrieval is advisory. Lexical FTS remains available and no
            # provider error may broaden filesystem access or break search.
            with self._lock:
                self._retry_after = monotonic() + self.unavailable_retry_seconds
            return []
        with self._lock:
            self._retry_after = 0.0
        if len(vectors) != len(corpus):
            return []
        ranked = sorted(
            (
                SearchHit(
                    path=hit.path,
                    line_start=hit.line_start,
                    line_end=hit.line_end,
                    content=hit.content,
                    score=self._cosine(query_vector, vector),
                )
                for hit, vector in zip(corpus, vectors, strict=True)
                if len(vector) == len(query_vector)
            ),
            key=lambda item: (-item.score, item.path, item.line_start),
        )
        unique: list[SearchHit] = []
        seen: set[str] = set()
        for hit in ranked:
            if not math.isfinite(hit.score):
                continue
            if hit.score < self.minimum_score:
                break
            if hit.path in seen:
                continue
            seen.add(hit.path)
            unique.append(hit)
            if len(unique) >= limit:
                break
        return unique

    def _document_vectors(
        self, corpus: Sequence[SearchHit]
    ) -> tuple[tuple[float, ...], ...]:
        missing: list[str] = []
        for hit in corpus:
            key = self._document_text(hit.content)
            if key not in self._vectors and key not in missing:
                missing.append(key)
        for start in range(0, len(missing), self.batch_size):
            texts = missing[start : start + self.batch_size]
            embedded = self.provider.embed(tuple(texts))
            if len(embedded) != len(texts):
                raise ValueError("embedding provider returned the wrong document count")
            for text, vector in zip(texts, embedded, strict=True):
                if self.vector_cache_size:
                    self._vectors[text] = vector
                    self._vectors.move_to_end(text)
                    while len(self._vectors) > self.vector_cache_size:
                        self._vectors.popitem(last=False)
        return tuple(self._vectors[self._document_text(hit.content)] for hit in corpus)

    @staticmethod
    def _query_text(text: str) -> str:
        return "Instruct: Retrieve document passages relevant to this query.\nQuery: " + text

    @staticmethod
    def _document_text(text: str) -> str:
        return "Document passage: " + text

    @staticmethod
    def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
        left_norm = math.sqrt(sum(item * item for item in left))
        right_norm = math.sqrt(sum(item * item for item in right))
        if not left_norm or not right_norm:
            return -1.0
        return sum(a * b for a, b in zip(left, right, strict=True)) / (
            left_norm * right_norm
        )
