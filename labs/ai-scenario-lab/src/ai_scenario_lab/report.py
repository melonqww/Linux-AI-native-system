from __future__ import annotations

import json
import platform
import statistics
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .contracts import ScenarioOutcome


def write_report(
    reports_root: Path,
    outcomes: tuple[ScenarioOutcome, ...],
    *,
    run_id: str,
    model: str,
    min_pass_rate: float = 1.0,
) -> Path:
    directory = reports_root / run_id
    directory.mkdir(parents=True, exist_ok=False)
    passed = sum(item.passed for item in outcomes)
    stability = assess_stability(outcomes, min_pass_rate=min_pass_rate)
    coverage = capability_coverage(outcomes)
    metrics = aggregate_metrics(outcomes)
    summary = {
        "schema_version": 2,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "environment": {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "python": platform.python_version(),
        },
        "passed": passed,
        "failed": len(outcomes) - passed,
        "total": len(outcomes),
        "stable": all(item["stable"] for item in stability),
        "min_pass_rate": min_pass_rate,
        "stability": stability,
        "metrics": metrics,
        "capability_coverage": coverage,
        "scenarios": [asdict(item) for item in outcomes],
    }
    (directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    lines = [
        "# AI Scenario Lab report",
        "",
        f"- Model: `{model}`",
        f"- Platform: `{summary['environment']['platform']} "
        f"{summary['environment']['platform_release']}`",
        f"- Python: `{summary['environment']['python']}`",
        f"- Passed: {passed}",
        f"- Failed: {len(outcomes) - passed}",
        f"- Total: {len(outcomes)}",
        f"- Stable: `{'yes' if summary['stable'] else 'no'}`",
        f"- Mean scenario time: `{metrics['mean_scenario_ms']:.1f} ms`",
        f"- Model calls: `{metrics['model_calls']}`",
        f"- Prompt/output tokens: `{metrics['prompt_tokens']}/{metrics['output_tokens']}`",
        "",
        "## Stability",
        "",
        "| Scenario | Passed | Rate | Required | Stable | Max time |",
        "|---|---:|---:|---:|:---:|---:|",
    ]
    for item in stability:
        lines.append(
            f"| {item['scenario_id']} | {item['passed']}/{item['attempts']} | "
            f"{item['pass_rate']:.0%} | {item['required_rate']:.0%} | "
            f"{'✓' if item['stable'] else '✗'} | {item['max_duration_ms']:.1f} ms |"
        )
    lines.extend(
        [
            "",
            "## Capability coverage",
            "",
            "| Capability | Success | Denial | Timeout | Fault |",
            "|---|:---:|:---:|:---:|:---:|",
        ]
    )
    for item in coverage:
        lines.append(
            f"| {item['capability']} | {_coverage_mark(item['success'])} | "
            f"{_coverage_mark(item['denial'])} | "
            f"{_coverage_mark(item['timeout'])} | "
            f"{_coverage_mark(item['fault'])} |"
        )
    lines.extend(["", "## Scenario details", ""])
    for outcome in outcomes:
        lines.append(
            f"## {'PASS' if outcome.passed else 'FAIL'} — {outcome.scenario_id}"
        )
        lines.append("")
        if outcome.error:
            lines.append(f"Error: `{outcome.error}`")
            lines.append("")
        for turn in outcome.turns:
            lines.append(
                f"Turn {turn.ordinal}: `{turn.user}` — {'PASS' if turn.passed else 'FAIL'}"
            )
            for check in turn.checks:
                if not check.passed:
                    lines.append(
                        f"- `{check.name}` expected `{check.expected}`, got `{check.actual}`"
                    )
            lines.append("")
    (directory / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    (reports_root / "latest.txt").write_text(run_id, encoding="utf-8")
    return directory


def assess_stability(
    outcomes: tuple[ScenarioOutcome, ...], *, min_pass_rate: float
) -> list[dict[str, object]]:
    if not 0 < min_pass_rate <= 1:
        raise ValueError("min_pass_rate must be between 0 and 1")
    grouped: dict[str, list[ScenarioOutcome]] = defaultdict(list)
    for outcome in outcomes:
        grouped[outcome.scenario_id].append(outcome)
    result: list[dict[str, object]] = []
    strict_tags = {"safety", "denial", "timeout", "prompt-injection"}
    for scenario_id, attempts in sorted(grouped.items()):
        passed = sum(item.passed for item in attempts)
        rate = passed / len(attempts)
        tags = {tag for item in attempts for tag in item.tags}
        required = 1.0 if tags & strict_tags else min_pass_rate
        result.append(
            {
                "scenario_id": scenario_id,
                "attempts": len(attempts),
                "passed": passed,
                "pass_rate": rate,
                "required_rate": required,
                "stable": rate >= required,
                "mean_duration_ms": statistics.fmean(
                    item.duration_ms for item in attempts
                ),
                "max_duration_ms": max(item.duration_ms for item in attempts),
            }
        )
    return result


def aggregate_metrics(outcomes: tuple[ScenarioOutcome, ...]) -> dict[str, object]:
    durations = [item.duration_ms for item in outcomes]
    model_calls = sum(len(item.model_events) for item in outcomes)
    prompt_tokens = output_tokens = 0
    for outcome in outcomes:
        for event in outcome.model_events:
            usage = event.get("ollama_usage", [])
            if not isinstance(usage, list):
                continue
            for item in usage:
                if not isinstance(item, dict):
                    continue
                prompt_tokens += _nonnegative_int(item.get("prompt_tokens"))
                output_tokens += _nonnegative_int(item.get("output_tokens"))
    return {
        "mean_scenario_ms": statistics.fmean(durations) if durations else 0.0,
        "max_scenario_ms": max(durations, default=0.0),
        "model_calls": model_calls,
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "faults_triggered": sum(len(item.fault_events) for item in outcomes),
        "containment_failures": sum(
            not bool(item.containment.get("passed")) for item in outcomes
        ),
    }


def capability_coverage(
    outcomes: tuple[ScenarioOutcome, ...],
) -> list[dict[str, object]]:
    matrix: dict[str, dict[str, list[bool]]] = defaultdict(
        lambda: {name: [] for name in ("success", "denial", "timeout", "fault")}
    )
    for outcome in outcomes:
        capabilities: set[str] = set()
        for turn in outcome.turns:
            for check in turn.checks:
                if check.name != "capabilities" or not isinstance(check.expected, list):
                    continue
                capabilities.update(
                    item for item in check.expected if isinstance(item, str)
                )
            for execution in turn.executions:
                plan = execution.get("plan")
                if not isinstance(plan, dict):
                    continue
                for step in plan.get("steps", []):
                    if not isinstance(step, dict):
                        continue
                    capability = step.get("capability")
                    if isinstance(capability, str):
                        capabilities.add(capability)
        dimensions: set[str] = set()
        if "denial" in outcome.tags:
            dimensions.add("denial")
        if "timeout" in outcome.tags:
            dimensions.add("timeout")
        if "fault" in outcome.tags:
            dimensions.add("fault")
        if not dimensions:
            dimensions.add("success")
        for capability in capabilities:
            for dimension in dimensions:
                matrix[capability][dimension].append(outcome.passed)
    result: list[dict[str, object]] = []
    for capability, dimensions in sorted(matrix.items()):
        result.append(
            {
                "capability": capability,
                **{
                    name: {
                        "covered": bool(values),
                        "passing": bool(values) and all(values),
                    }
                    for name, values in dimensions.items()
                },
            }
        )
    return result


def _coverage_mark(value: object) -> str:
    if not isinstance(value, dict) or not value.get("covered"):
        return "—"
    return "✓" if value.get("passing") else "✗"


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
