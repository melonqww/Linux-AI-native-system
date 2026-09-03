"""Fast local candidate selection over module-owned intent descriptions."""

from __future__ import annotations

import re
import unicodedata
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
        candidate_margin: float = 0.15,
        max_candidates: int = 3,
    ) -> None:
        if not 0.1 <= minimum_score <= 0.9:
            raise ValueError("minimum_score must be between 0.1 and 0.9")
        if not 1 <= max_candidates <= 8:
            raise ValueError("max_candidates must be from 1 to 8")
        if not 0.05 <= candidate_margin <= 0.3:
            raise ValueError("candidate_margin must be between 0.05 and 0.3")
        self.descriptors = tuple(descriptors)
        self.minimum_score = minimum_score
        self.candidate_margin = candidate_margin
        self.max_candidates = max_candidates

    def candidates(self, text: str) -> tuple[CapabilityMatch, ...]:
        normalized = self._normalize(text)
        if not normalized:
            return ()
        matches: list[CapabilityMatch] = []
        anchored_operations: set[str] = set()
        for descriptor in self.descriptors:
            samples = (descriptor.description, *descriptor.examples)
            score = max(
                self._similarity(normalized, self._normalize(item)) for item in samples
            )
            if score >= self.minimum_score:
                matches.append(
                    CapabilityMatch(
                        descriptor.capability_id,
                        descriptor.operation,
                        round(score, 6),
                    )
                )
            for example in descriptor.examples:
                normalized_example = self._normalize(example)
                example_tokens = normalized_example.split()
                if (
                    example_tokens
                    and example_tokens[0] in normalized.split()
                    and self._similarity(normalized, normalized_example) >= 0.45
                ):
                    anchored_operations.add(descriptor.operation)
        matches.sort(key=lambda item: (-item.score, item.operation, item.capability_id))
        relative_cutoff = (
            max(
                self.minimum_score,
                matches[0].score - self.candidate_margin,
            )
            if matches
            else self.minimum_score
        )
        selected: list[CapabilityMatch] = []
        seen_operations: set[str] = set()
        for match in matches:
            if (
                match.score < relative_cutoff
                and match.operation not in anchored_operations
            ):
                continue
            if match.operation in seen_operations:
                continue
            selected.append(match)
            seen_operations.add(match.operation)
            if len(selected) >= self.max_candidates:
                break
        return tuple(selected)

    def available_operations(self) -> tuple[str, ...]:
        """Return only operations declared by the currently enabled modules."""
        return tuple(dict.fromkeys(item.operation for item in self.descriptors))

    def requested_operations(self, text: str) -> tuple[str, ...]:
        """Conservative action evidence, distinct from broad topical retrieval.

        Module examples must start with a request cue (imperative/refinement).
        Absence is uncertainty, never permission to expose every tool. Matching
        tolerates one typo in longer cues, not arbitrary semantic similarity.
        Quoted examples do not authorize operations.
        """
        visible = re.sub(r'"[^"\n]*"|«[^»]*»|`[^`]*`', " ", text)
        tokens = self._normalize(visible).split()
        result = []
        for descriptor in self.descriptors:
            cues = {
                self._normalize(example).split()[0]
                for example in descriptor.examples
                if self._normalize(example)
            }
            for index, token in enumerate(tokens):
                if any(
                    word in {"не", "not", "never", "without", "dont"}
                    for word in tokens[max(0, index - 3) : index]
                ):
                    continue
                if any(
                    token == cue
                    or (
                        min(len(token), len(cue)) >= 4
                        and token[0] == cue[0]
                        and self._damerau_levenshtein(token, cue) <= 1
                    )
                    for cue in cues
                ):
                    result.append(descriptor.operation)
                    break
        return tuple(dict.fromkeys(result))

    @staticmethod
    def _normalize(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("capability query must be text")
        folded = unicodedata.normalize("NFKC", value).casefold()
        return " ".join(_WORD.findall(folded))

    @classmethod
    def _similarity(cls, query: str, sample: str) -> float:
        query_tokens = tuple(query.split())
        sample_tokens = tuple(sample.split())
        if not query_tokens or not sample_tokens:
            return 0.0

        token_scores = tuple(
            max(
                cls._token_similarity(sample_token, query_token)
                for query_token in query_tokens
            )
            for sample_token in sample_tokens
        )
        token_coverage = sum(token_scores) / len(token_scores)
        strong_coverage = sum(score >= 0.72 for score in token_scores) / len(
            token_scores
        )

        sample_grams = cls._ngrams(sample)
        window_score = max(
            (
                cls._gram_similarity(" ".join(window), sample_grams)
                for window in cls._token_windows(query_tokens, len(sample_tokens))
            ),
            default=0.0,
        )
        return min(
            1.0,
            0.55 * token_coverage + 0.15 * strong_coverage + 0.30 * window_score,
        )

    @classmethod
    def _token_similarity(cls, left: str, right: str) -> float:
        if left == right:
            return 1.0
        if min(len(left), len(right)) < 4:
            return 0.0
        distance_score = 1.0 - cls._damerau_levenshtein(left, right) / max(
            len(left), len(right)
        )
        left_grams = cls._ngrams(left)
        right_grams = cls._ngrams(right)
        dice_score = (
            2 * len(left_grams & right_grams) / (len(left_grams) + len(right_grams))
            if left_grams and right_grams
            else 0.0
        )
        return max(0.0, distance_score, dice_score)

    @staticmethod
    def _token_windows(
        tokens: tuple[str, ...], sample_size: int
    ) -> tuple[tuple[str, ...], ...]:
        minimum = max(1, sample_size - 2)
        maximum = min(len(tokens), sample_size + 2)
        return tuple(
            tokens[start : start + size]
            for size in range(minimum, maximum + 1)
            for start in range(0, len(tokens) - size + 1)
        )

    @classmethod
    def _gram_similarity(cls, value: str, expected: set[str]) -> float:
        actual = cls._ngrams(value)
        return (
            len(actual & expected) / len(actual | expected)
            if actual and expected
            else 0.0
        )

    @staticmethod
    def _damerau_levenshtein(left: str, right: str) -> int:
        """Bounded-input optimal-string-alignment distance for token typos."""
        previous_previous: list[int] | None = None
        previous = list(range(len(right) + 1))
        for left_index, left_character in enumerate(left, start=1):
            current = [left_index]
            for right_index, right_character in enumerate(right, start=1):
                current.append(
                    min(
                        current[right_index - 1] + 1,
                        previous[right_index] + 1,
                        previous[right_index - 1] + (left_character != right_character),
                    )
                )
                if (
                    previous_previous is not None
                    and left_index > 1
                    and right_index > 1
                    and left_character == right[right_index - 2]
                    and left[left_index - 2] == right_character
                ):
                    current[right_index] = min(
                        current[right_index], previous_previous[right_index - 2] + 1
                    )
            previous_previous, previous = previous, current
        return previous[-1]

    @staticmethod
    def _ngrams(value: str) -> set[str]:
        compact = f"  {value}  "
        return {compact[index : index + 3] for index in range(len(compact) - 2)}
