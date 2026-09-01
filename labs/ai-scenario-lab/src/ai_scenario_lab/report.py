from __future__ import annotations

import json
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
) -> Path:
    directory = reports_root / run_id
    directory.mkdir(parents=True, exist_ok=False)
    passed = sum(item.passed for item in outcomes)
    summary = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "passed": passed,
        "failed": len(outcomes) - passed,
        "total": len(outcomes),
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
        f"- Passed: {passed}",
        f"- Failed: {len(outcomes) - passed}",
        f"- Total: {len(outcomes)}",
        "",
    ]
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
