"""Advisory multilingual capability retrieval over module-owned descriptions."""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Callable, Iterable
from threading import RLock
from time import monotonic
from .capability_router import CapabilityCandidateRouter
from .contracts import CapabilityDescriptor, CapabilityMatch
from .embedding import EmbeddingProvider


_QUERY_PREFIX = (
    "Instruct: Retrieve operating-system capabilities that can fulfill this user request.\n"
    "Query: "
)
_DOCUMENT_PREFIX = "Operating-system capability: "


class SemanticCapabilitySelector:
    """Returns candidates only; model failure produces no semantic candidates."""

    def __init__(
        self,
        descriptors: Iterable[CapabilityDescriptor],
        provider: EmbeddingProvider,
        *,
        minimum_score: float = 0.55,
        candidate_margin: float = 0.08,
        max_candidates: int = 3,
        query_cache_size: int = 128,
        unavailable_retry_seconds: float = 60.0,
        monotonic_fn: Callable[[], float] = monotonic,
    ) -> None:
        values = tuple(descriptors)
        if not values or len(values) > 128:
            raise ValueError("semantic descriptors must contain from 1 to 128 entries")
        if len({item.operation for item in values}) != len(values):
            raise ValueError("semantic descriptor operations must be unique")
        if not 0 <= minimum_score <= 1:
            raise ValueError("semantic minimum_score must be from 0 to 1")
        if not 0 <= candidate_margin <= 0.3:
            raise ValueError("semantic candidate_margin must be from 0 to 0.3")
        if not 1 <= max_candidates <= 8:
            raise ValueError("semantic max_candidates must be from 1 to 8")
        if not 0 <= query_cache_size <= 1_024:
            raise ValueError("semantic query_cache_size must be from 0 to 1024")
        if not 1 <= unavailable_retry_seconds <= 600:
            raise ValueError("semantic retry delay must be from 1 to 600 seconds")
        self.descriptors = values
        self.provider = provider
        self.minimum_score = float(minimum_score)
        self.candidate_margin = float(candidate_margin)
        self.max_candidates = max_candidates
        self.query_cache_size = query_cache_size
        self.unavailable_retry_seconds = float(unavailable_retry_seconds)
        self._monotonic = monotonic_fn
        self._descriptor_vectors: tuple[tuple[float, ...], ...] | None = None
        self._query_cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._retry_after = 0.0
        self._lock = RLock()

    def candidates(self, text: str) -> tuple[CapabilityMatch, ...]:
        query = self._query_text(text)
        if query is None:
            return ()
        with self._lock:
            if self._monotonic() < self._retry_after:
                return ()
        try:
            descriptor_vectors = self._descriptors()
            query_vector = self._query_vector(query)
        except Exception:
            with self._lock:
                self._retry_after = self._monotonic() + self.unavailable_retry_seconds
            return ()
        with self._lock:
            self._retry_after = 0.0
        if any(len(item) != len(query_vector) for item in descriptor_vectors):
            return ()
        matches = sorted(
            (
                CapabilityMatch(
                    descriptor.capability_id,
                    descriptor.operation,
                    round(self._cosine(query_vector, vector), 6),
                )
                for descriptor, vector in zip(
                    self.descriptors, descriptor_vectors, strict=True
                )
            ),
            key=lambda item: (-item.score, item.operation, item.capability_id),
        )
        if not matches or matches[0].score < self.minimum_score:
            return ()
        cutoff = max(self.minimum_score, matches[0].score - self.candidate_margin)
        return tuple(
            item for item in matches if item.score >= cutoff
        )[: self.max_candidates]

    def available_operations(self) -> tuple[str, ...]:
        return tuple(item.operation for item in self.descriptors)

    def _descriptors(self) -> tuple[tuple[float, ...], ...]:
        with self._lock:
            if self._descriptor_vectors is None:
                documents = tuple(
                    _DOCUMENT_PREFIX
                    + descriptor.description
                    + "\nExamples: "
                    + " | ".join(descriptor.examples[:8])
                    for descriptor in self.descriptors
                )
                self._descriptor_vectors = self.provider.embed(documents)
                if len(self._descriptor_vectors) != len(self.descriptors):
                    self._descriptor_vectors = None
                    raise ValueError("embedding provider returned the wrong descriptor count")
            return self._descriptor_vectors

    def _query_vector(self, query: str) -> tuple[float, ...]:
        with self._lock:
            cached = self._query_cache.get(query)
            if cached is not None:
                self._query_cache.move_to_end(query)
                return cached
        vectors = self.provider.embed((_QUERY_PREFIX + query,))
        if len(vectors) != 1:
            raise ValueError("embedding provider returned the wrong query count")
        vector = vectors[0]
        if self.query_cache_size:
            with self._lock:
                self._query_cache[query] = vector
                self._query_cache.move_to_end(query)
                while len(self._query_cache) > self.query_cache_size:
                    self._query_cache.popitem(last=False)
        return vector

    @staticmethod
    def _query_text(value: str) -> str | None:
        if not isinstance(value, str):
            raise TypeError("semantic capability query must be text")
        value = value.strip()
        if not value:
            return None
        if len(value) > 4_000 or any(
            ord(character) < 32 and character not in "\n\t" for character in value
        ):
            raise ValueError("semantic capability query is invalid")
        return value

    @staticmethod
    def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
        left_norm = math.sqrt(sum(item * item for item in left))
        right_norm = math.sqrt(sum(item * item for item in right))
        if not left_norm or not right_norm:
            return -1.0
        return sum(a * b for a, b in zip(left, right, strict=True)) / (
            left_norm * right_norm
        )


class HybridCapabilityRouter:
    """Combines deterministic typo tolerance with optional semantic recall."""

    def __init__(
        self,
        lexical: CapabilityCandidateRouter,
        semantic: SemanticCapabilitySelector,
        *,
        max_candidates: int = 3,
    ) -> None:
        if lexical.available_operations() != semantic.available_operations():
            raise ValueError("lexical and semantic operation catalogs differ")
        if not 1 <= max_candidates <= 8:
            raise ValueError("hybrid max_candidates must be from 1 to 8")
        self.lexical = lexical
        self.semantic = semantic
        self.max_candidates = max_candidates

    def candidates(self, text: str) -> tuple[CapabilityMatch, ...]:
        merged: dict[str, CapabilityMatch] = {}
        for match in (*self.lexical.candidates(text), *self.semantic.candidates(text)):
            current = merged.get(match.operation)
            if current is None or match.score > current.score:
                merged[match.operation] = match
        return tuple(
            sorted(
                merged.values(),
                key=lambda item: (-item.score, item.operation, item.capability_id),
            )[: self.max_candidates]
        )

    def available_operations(self) -> tuple[str, ...]:
        return self.lexical.available_operations()

    def requested_operations(self, text: str) -> tuple[str, ...]:
        lexical = self.lexical.requested_operations(text)
        semantic = tuple(item.operation for item in self.semantic.candidates(text))
        return tuple(dict.fromkeys((*lexical, *semantic)))
