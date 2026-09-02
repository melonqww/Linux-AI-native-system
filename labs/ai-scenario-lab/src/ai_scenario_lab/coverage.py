"""Multi-dimensional coverage accounting for realistic user journeys."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from .diagnostics import DiagnosticContext


COVERAGE_DIMENSIONS = (
    "language",
    "behavior",
    "mode",
    "memory_depth",
    "capability",
    "decision",
    "failure_kind",
    "input_modality",
)


@dataclass(frozen=True)
class CoverageObservation:
    context: DiagnosticContext
    passed: bool
    scenario_id: str = "unknown"


def coverage_taxonomy(
    observations: Iterable[CoverageObservation],
    *,
    intersections: tuple[tuple[str, ...], ...] = (),
) -> dict[str, object]:
    """Aggregate each axis independently and selected cross-axis intersections.

    Cross-axis rows are opt-in to keep reports bounded. Counts, rather than a
    boolean "covered", preserve flaky behavior across repeated attempts.
    """

    items = tuple(observations)
    axes: dict[str, list[dict[str, object]]] = {}
    for dimension in COVERAGE_DIMENSIONS:
        axes[dimension] = _rows(items, (dimension,))

    cross: dict[str, list[dict[str, object]]] = {}
    for dimensions in intersections:
        if not dimensions or any(
            item not in COVERAGE_DIMENSIONS for item in dimensions
        ):
            raise ValueError("intersection contains an unknown coverage dimension")
        if len(set(dimensions)) != len(dimensions):
            raise ValueError("intersection dimensions must be unique")
        cross[" × ".join(dimensions)] = _rows(items, dimensions)

    return {
        "schema_version": 1,
        "attempts": len(items),
        "passed": sum(item.passed for item in items),
        "failed": sum(not item.passed for item in items),
        "axes": axes,
        "intersections": cross,
    }


def _rows(
    observations: tuple[CoverageObservation, ...], dimensions: tuple[str, ...]
) -> list[dict[str, object]]:
    grouped: dict[tuple[str | int, ...], list[CoverageObservation]] = defaultdict(list)
    for observation in observations:
        values = observation.context.dimensions()
        grouped[tuple(values[name] for name in dimensions)].append(observation)

    rows: list[dict[str, object]] = []
    for key, attempts in sorted(
        grouped.items(), key=lambda item: tuple(map(str, item[0]))
    ):
        passed = sum(item.passed for item in attempts)
        row: dict[str, object] = {
            name: value for name, value in zip(dimensions, key, strict=True)
        }
        row.update(
            {
                "attempts": len(attempts),
                "passed": passed,
                "failed": len(attempts) - passed,
                "pass_rate": passed / len(attempts),
                "scenario_count": len({item.scenario_id for item in attempts}),
            }
        )
        rows.append(row)
    return rows
