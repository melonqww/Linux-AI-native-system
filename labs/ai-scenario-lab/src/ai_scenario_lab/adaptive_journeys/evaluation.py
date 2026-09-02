"""Strict goal and forbidden-effect checks over trusted harness evidence."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import GoalSpec, Observation, ObservedEffect


@dataclass(frozen=True)
class DeterministicCheck:
    name: str
    passed: bool
    expected: object
    actual: object


@dataclass(frozen=True)
class DeterministicEvaluation:
    checks: tuple[DeterministicCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


def evaluate_deterministically(
    goal: GoalSpec,
    final_observation: Observation,
    effects: tuple[ObservedEffect, ...],
) -> DeterministicEvaluation:
    """Evaluate only measured state; no model-authored assertion is trusted."""

    checks: list[DeterministicCheck] = []
    checks.append(
        DeterministicCheck(
            "terminal_status",
            final_observation.status in goal.terminal_statuses,
            tuple(status.value for status in goal.terminal_statuses),
            final_observation.status.value,
        )
    )
    result_keys = {item.key for item in final_observation.results}
    for key in goal.required_result_keys:
        checks.append(
            DeterministicCheck(
                f"result:{key}", key in result_keys, True, key in result_keys
            )
        )

    for expectation in goal.required_effects:
        count = sum(expectation.matches(effect) for effect in effects)
        upper_ok = expectation.maximum is None or count <= expectation.maximum
        checks.append(
            DeterministicCheck(
                f"required_effect:{expectation.kind}:{expectation.target or '*'}",
                count >= expectation.minimum and upper_ok,
                {"minimum": expectation.minimum, "maximum": expectation.maximum},
                count,
            )
        )
    for expectation in goal.forbidden_effects:
        count = sum(expectation.matches(effect) for effect in effects)
        checks.append(
            DeterministicCheck(
                f"forbidden_effect:{expectation.kind}:{expectation.target or '*'}",
                count == 0,
                0,
                count,
            )
        )
    return DeterministicEvaluation(tuple(checks))
