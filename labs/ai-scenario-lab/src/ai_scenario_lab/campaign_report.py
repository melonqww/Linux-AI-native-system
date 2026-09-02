"""Durable reports for repeated contract scenarios and adaptive journeys."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from .coverage import CoverageObservation, coverage_taxonomy
from .diagnostics import Diagnostic, DiagnosticContext, ProblemLayer


_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_FINGERPRINT = re.compile(r"diag-v1-[0-9a-f]{24}")
_KINDS = frozenset({"scenario", "journey"})
_INTERSECTIONS = (
    ("language", "memory_depth"),
    ("mode", "capability"),
    ("decision", "failure_kind"),
)


@dataclass(frozen=True)
class CampaignAttempt:
    attempt_id: str
    subject_id: str
    kind: str
    context: DiagnosticContext
    passed: bool
    duration_ms: float
    diagnostic: Diagnostic | None = None
    trace_path: str | None = None

    def __post_init__(self) -> None:
        for name in ("attempt_id", "subject_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError(
                    f"{name} must be non-empty text of at most 200 characters"
                )
            if any(ord(char) < 32 for char in value):
                raise ValueError(f"{name} contains control characters")
        if self.kind not in _KINDS:
            raise ValueError("kind must be 'scenario' or 'journey'")
        if not isinstance(self.context, DiagnosticContext):
            raise TypeError("context must be DiagnosticContext")
        for name, value in self.context.dimensions().items():
            if isinstance(value, str) and (
                len(value) > 300 or any(ord(char) < 32 for char in value)
            ):
                raise ValueError(f"context dimension {name} is invalid")
        if not isinstance(self.passed, bool):
            raise TypeError("passed must be boolean")
        if (
            isinstance(self.duration_ms, bool)
            or not isinstance(self.duration_ms, (int, float))
            or self.duration_ms < 0
            or self.duration_ms == float("inf")
            or self.duration_ms != self.duration_ms
        ):
            raise ValueError("duration_ms must be finite and non-negative")
        if self.diagnostic is not None:
            if not isinstance(self.diagnostic, Diagnostic):
                raise TypeError("diagnostic must be Diagnostic or None")
            if not isinstance(self.diagnostic.layer, ProblemLayer):
                raise TypeError("diagnostic layer must be ProblemLayer")
            if not _FINGERPRINT.fullmatch(self.diagnostic.fingerprint):
                raise ValueError("diagnostic fingerprint is invalid")
            if not isinstance(self.diagnostic.rule, str) or not self.diagnostic.rule:
                raise ValueError("diagnostic rule is invalid")
            try:
                json.dumps(self.diagnostic.evidence)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "diagnostic evidence must be JSON serializable"
                ) from error
            if self.diagnostic.context != self.context:
                raise ValueError("diagnostic context must match attempt context")
        if self.passed and self.diagnostic is not None:
            raise ValueError("a passing attempt cannot have a failure diagnostic")
        if self.trace_path is not None:
            if (
                not isinstance(self.trace_path, str)
                or not self.trace_path.strip()
                or len(self.trace_path) > 4_096
                or any(ord(char) < 32 for char in self.trace_path)
            ):
                raise ValueError("trace_path must be non-empty printable text")


def write_campaign_report(
    reports_root: Path,
    attempts: Iterable[CampaignAttempt],
    *,
    run_id: str,
) -> Path:
    """Validate and safely publish a self-contained campaign report directory."""

    if not isinstance(reports_root, Path):
        raise TypeError("reports_root must be a Path")
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id is invalid")
    normalized = tuple(attempts)
    if not normalized:
        raise ValueError("campaign must contain at least one attempt")
    if any(not isinstance(item, CampaignAttempt) for item in normalized):
        raise TypeError("attempts must contain only CampaignAttempt values")
    attempt_ids = [item.attempt_id for item in normalized]
    if len(attempt_ids) != len(set(attempt_ids)):
        raise ValueError("attempt_id values must be unique")

    reports_root.mkdir(parents=True, exist_ok=True)
    destination = reports_root / run_id
    if destination.exists():
        raise FileExistsError(destination)
    temporary = reports_root / f".{run_id}.tmp-{uuid4().hex}"
    temporary.mkdir()
    try:
        payload = _summary(run_id, normalized)
        _write_json(temporary / "summary.json", payload)
        _write_text(temporary / "summary.md", _summary_markdown(payload))
        _write_text(temporary / "failures.md", _failures_markdown(payload))
        os.replace(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


def _summary(run_id: str, attempts: tuple[CampaignAttempt, ...]) -> dict[str, object]:
    coverage = coverage_taxonomy(
        (
            CoverageObservation(item.context, item.passed, item.subject_id)
            for item in attempts
        ),
        intersections=_INTERSECTIONS,
    )
    failures = [item for item in attempts if not item.passed]
    grouped: dict[str, list[CampaignAttempt]] = {}
    unknown: list[CampaignAttempt] = []
    for attempt in failures:
        if (
            attempt.diagnostic is None
            or attempt.diagnostic.layer is ProblemLayer.UNKNOWN
        ):
            unknown.append(attempt)
            continue
        grouped.setdefault(attempt.diagnostic.fingerprint, []).append(attempt)

    groups = [
        _failure_group(fingerprint, values) for fingerprint, values in grouped.items()
    ]
    groups.sort(key=lambda item: (-int(item["count"]), str(item["fingerprint"])))
    return {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "attempts": len(attempts),
        "passed": sum(item.passed for item in attempts),
        "failed": len(failures),
        "duration_ms": sum(float(item.duration_ms) for item in attempts),
        "coverage": coverage,
        "failure_groups": groups,
        "unknown_failures": [_attempt_payload(item) for item in unknown],
        "results": [_attempt_payload(item) for item in attempts],
    }


def _failure_group(
    fingerprint: str, attempts: list[CampaignAttempt]
) -> dict[str, object]:
    diagnostic = attempts[0].diagnostic
    assert diagnostic is not None
    return {
        "fingerprint": fingerprint,
        "layer": diagnostic.layer.value,
        "rule": diagnostic.rule,
        "count": len(attempts),
        "attempts": [_attempt_payload(item) for item in attempts],
    }


def _attempt_payload(attempt: CampaignAttempt) -> dict[str, object]:
    return {
        "attempt_id": attempt.attempt_id,
        "subject_id": attempt.subject_id,
        "kind": attempt.kind,
        "passed": attempt.passed,
        "duration_ms": float(attempt.duration_ms),
        "context": attempt.context.dimensions(),
        "diagnostic": _diagnostic_payload(attempt.diagnostic),
        "trace_path": attempt.trace_path,
    }


def _diagnostic_payload(diagnostic: Diagnostic | None) -> dict[str, object] | None:
    if diagnostic is None:
        return None
    payload = asdict(diagnostic)
    payload["layer"] = diagnostic.layer.value
    return payload


def _summary_markdown(summary: dict[str, object]) -> str:
    coverage = summary["coverage"]
    assert isinstance(coverage, dict)
    lines = [
        "# AI Scenario Lab campaign",
        "",
        f"- Run: `{_md(summary['run_id'])}`",
        f"- Attempts: {summary['attempts']}",
        f"- Passed: {summary['passed']}",
        f"- Failed: {summary['failed']}",
        f"- Duration: {float(summary['duration_ms']):.1f} ms",
        "",
        "## Multidimensional coverage",
        "",
    ]
    axes = coverage["axes"]
    assert isinstance(axes, dict)
    for dimension, rows in axes.items():
        lines.extend(
            (
                f"### {_md(dimension)}",
                "",
                "| Value | Pass | Fail | Rate |",
                "|---|---:|---:|---:|",
            )
        )
        for row in rows:
            lines.append(
                f"| {_md(row[dimension])} | {row['passed']} | {row['failed']} | "
                f"{float(row['pass_rate']):.0%} |"
            )
        lines.append("")
    intersections = coverage["intersections"]
    assert isinstance(intersections, dict)
    lines.extend(("## Coverage intersections", ""))
    for label, rows in intersections.items():
        dimensions = label.split(" × ")
        headings = " | ".join(_md(item) for item in dimensions)
        lines.extend(
            (
                f"### {_md(label)}",
                "",
                f"| {headings} | Pass | Fail | Rate |",
                "|" + "---|" * len(dimensions) + "---:|---:|---:|",
            )
        )
        for row in rows:
            values = " | ".join(_md(row[item]) for item in dimensions)
            lines.append(
                f"| {values} | {row['passed']} | {row['failed']} | "
                f"{float(row['pass_rate']):.0%} |"
            )
        lines.append("")
    lines.extend(
        (
            "## Failure overview",
            "",
            f"- Fingerprinted groups: {len(summary['failure_groups'])}",
            f"- Unknown failures: {len(summary['unknown_failures'])}",
            "",
        )
    )
    return "\n".join(lines)


def _failures_markdown(summary: dict[str, object]) -> str:
    lines = ["# Campaign failures", ""]
    groups = summary["failure_groups"]
    unknown = summary["unknown_failures"]
    assert isinstance(groups, list) and isinstance(unknown, list)
    if not groups and not unknown:
        return "# Campaign failures\n\nNo failures.\n"
    for group in groups:
        lines.extend(
            (
                f"## {_md(group['layer'])} — `{_md(group['fingerprint'])}`",
                "",
                f"- Rule: `{_md(group['rule'])}`",
                f"- Count: {group['count']}",
                "",
            )
        )
        lines.extend(_attempt_lines(group["attempts"]))
    if unknown:
        lines.extend(
            (
                "## UNKNOWN",
                "",
                "Failures without a recognized deterministic fingerprint.",
                "",
            )
        )
        lines.extend(_attempt_lines(unknown))
    return "\n".join(lines)


def _attempt_lines(attempts: object) -> list[str]:
    assert isinstance(attempts, list)
    lines: list[str] = []
    for attempt in attempts:
        trace = attempt.get("trace_path")
        trace_text = f" — [trace]({_md_link(trace)})" if isinstance(trace, str) else ""
        context = attempt["context"]
        lines.append(
            f"- `{_md(attempt['attempt_id'])}` ({_md(attempt['kind'])}: "
            f"{_md(attempt['subject_id'])}; {context['language']}; memory "
            f"{context['memory_depth']}){trace_text}"
        )
    lines.append("")
    return lines


def _write_json(path: Path, payload: object) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _md(value: object) -> str:
    return str(value).replace("|", "\\|").replace("`", "\\`")


def _md_link(value: str) -> str:
    return (
        value.replace("\\", "/")
        .replace("%", "%25")
        .replace("(", "%28")
        .replace(")", "%29")
        .replace(" ", "%20")
        .replace("#", "%23")
    )
