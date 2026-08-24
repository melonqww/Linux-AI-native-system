"""Fast local candidate selection over module-owned intent descriptions."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .contracts import CapabilityDescriptor, CapabilityMatch


_WORD = re.compile(r"[^\W_]+", re.UNICODE)


class CapabilityCandidateRouter:
    """Selects candidates only; it never creates plans or executes capabilities."""

    def __init__(
        self,
        descriptors: Iterable[CapabilityDescriptor],
        *,
        minimum_score: float = 0.28,
        max_candidates: int = 3,
    ) -> None:
        if not 0.1 <= minimum_score <= 0.9:
            raise ValueError("minimum_score must be between 0.1 and 0.9")
        if not 1 <= max_candidates <= 8:
            raise ValueError("max_candidates must be from 1 to 8")
        self.descriptors = tuple(descriptors)
        self.minimum_score = minimum_score
        self.max_candidates = max_candidates

    def candidates(self, text: str) -> tuple[CapabilityMatch, ...]:
        normalized = self._normalize(text)
        if not normalized:
            return ()
        matches: list[CapabilityMatch] = []
        for descriptor in self.descriptors:
            samples = (descriptor.description, *descriptor.examples)
            score = max(self._similarity(normalized, self._normalize(item)) for item in samples)
            if score >= self.minimum_score:
                matches.append(
                    CapabilityMatch(
                        descriptor.capability_id,
                        descriptor.operation,
                        round(score, 6),
                    )
                )
        matches.sort(key=lambda item: (-item.score, item.operation, item.capability_id))
        selected: list[CapabilityMatch] = []
        seen_operations: set[str] = set()
        for match in matches:
            if match.operation in seen_operations:
                continue
            selected.append(match)
            seen_operations.add(match.operation)
            if len(selected) >= self.max_candidates:
                break
        return tuple(selected)

    @staticmethod
    def _normalize(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("capability query must be text")
        return " ".join(_WORD.findall(value.casefold()))

    @classmethod
    def _similarity(cls, query: str, sample: str) -> float:
        query_tokens = set(query.split())
        sample_tokens = set(sample.split())
        if not query_tokens or not sample_tokens:
            return 0.0
        shared = query_tokens & sample_tokens
        token_coverage = len(shared) / len(sample_tokens)
        token_precision = len(shared) / len(query_tokens)
        query_grams = cls._ngrams(query)
        sample_grams = cls._ngrams(sample)
        gram_score = (
            len(query_grams & sample_grams) / len(query_grams | sample_grams)
            if query_grams and sample_grams
            else 0.0
        )
        return min(1.0, 0.55 * token_coverage + 0.2 * token_precision + 0.25 * gram_score)

    @staticmethod
    def _ngrams(value: str) -> set[str]:
        compact = f"  {value}  "
        return {compact[index : index + 3] for index in range(len(compact) - 2)}
