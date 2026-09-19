from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
RUN_ID_RE = re.compile(r"^\d{8}T\d{6}\.\d{6}Z$")
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:,\-]{0,159}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONTEXT_FIELDS = (
    "language",
    "behavior",
    "mode",
    "memory_depth",
    "capability",
    "decision",
    "failure_kind",
    "input_modality",
)
EVIDENCE_FIELDS = ("code", "component", "check_name", "stage")
COUNT_FIELDS = ("passed", "failed", "error", "not_run")
SOURCE_FILES = ("manifest.json", "summary.json", "failures.json")


class UnsafeEvidence(ValueError):
    """Raised when a public export contains an unexpected value."""


def export_foundation_history(reports_root: Path, output_root: Path) -> list[Path]:
    """Export public Foundation evidence using an explicit field allowlist.

    Raw prompts, model prose, traces, host paths, process IDs and network
    endpoints never enter the public object in the first place. Validation is a
    second boundary, not a best-effort recursive redaction pass.
    """

    run_directories = sorted(
        path
        for path in reports_root.iterdir()
        if path.is_dir() and RUN_ID_RE.fullmatch(path.name)
    )
    if not run_directories:
        raise FileNotFoundError(f"no Foundation reports found in {reports_root}")

    foundation_root = output_root / "foundation"
    foundation_root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    index_runs: list[dict[str, Any]] = []
    public_runs: list[dict[str, Any]] = []
    for run_directory in run_directories:
        public_run = build_public_run(run_directory)
        public_runs.append(public_run)
        destination = foundation_root / f"{run_directory.name}.json"
        _write_json(destination, public_run)
        written.append(destination)
        outcome = public_run["outcome"]
        index_runs.append(
            {
                "run_id": public_run["run_id"],
                "artifact": f"foundation/{run_directory.name}.json",
                "source_fingerprint": public_run["provenance"][
                    "source_fingerprint"
                ],
                "state": outcome["state"],
                "verdict": outcome["verdict"],
                "counts": outcome["counts"],
                "wall_time_seconds": outcome["wall_time_seconds"],
            }
        )

    index = {
        "schema_version": SCHEMA_VERSION,
        "evidence_kind": "ai-scenario-lab-foundation-history",
        "privacy": {
            "policy": "explicit-field-allowlist",
            "included": [
                "run identity and source fingerprint",
                "normalized runtime versions",
                "aggregate outcomes and coverage",
                "safe case identifiers, statuses and durations",
                "structured diagnostic codes",
            ],
            "excluded": [
                "user prompts and model prose",
                "trace and log contents",
                "absolute and virtual filesystem paths",
                "hostnames, usernames, process IDs and network endpoints",
            ],
        },
        "runs": index_runs,
    }
    index_path = output_root / "index.json"
    _write_json(index_path, index)
    written.insert(0, index_path)
    table_path = output_root / "README.md"
    table_path.write_text(
        render_public_results(index, public_runs), encoding="utf-8"
    )
    written.insert(1, table_path)
    return written


def render_public_results(
    index: dict[str, Any], public_runs: list[dict[str, Any]]
) -> str:
    """Render sanitized evidence as GitHub-friendly Markdown tables."""

    lines = [
        "# Публичные доказательства AI Scenario Lab",
        "",
        "Здесь находятся очищенные результаты реальных Foundation-прогонов.",
        "Файл автоматически строится только из публичных JSON в этой папке и не",
        "запускает модель, лабораторию или тесты.",
        "",
        "В таблицы входят результаты, покрытие, длительность и безопасная",
        "структурированная диагностика. Сообщения пользователя, ответы модели,",
        "traces, локальные пути, данные хоста, PID и сетевые адреса исключены",
        "строгим списком разрешённых полей.",
        "",
        "После нового Foundation-прогона таблицы и JSON обновляются командой",
        "`python tools/export_public_evidence.py` из папки лаборатории.",
        "",
        "## Все Foundation-прогоны",
        "",
        "| Run | Среда | Результат | Не запущено | Время | Verdict | JSON |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for item, run in zip(index["runs"], public_runs, strict=True):
        counts = item["counts"]
        runtime = run["runtime"]
        lines.append(
            "| `{run_id}` | {platform}, {model}, {context} tokens | "
            "{passed}/{planned} passed; {failed} failed; {error} error | "
            "{not_run} | {wall} | `{verdict}` | [открыть]({artifact}) |".format(
                run_id=item["run_id"],
                platform=runtime["platform"],
                model=runtime["model"],
                context=runtime["context_tokens"],
                passed=counts["passed"],
                planned=run["outcome"]["planned"],
                failed=counts["failed"],
                error=counts["error"],
                not_run=counts["not_run"],
                wall=_human_duration(item["wall_time_seconds"]),
                verdict=item["verdict"],
                artifact=item["artifact"],
            )
        )

    latest = public_runs[-1]
    lines.extend(
        [
            "",
            f"## Последний полный прогон — `{latest['run_id']}`",
            "",
            "### Покрытие",
            "",
            "| Ось | Значение | Passed | Failed | Error | Not run |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for axis, buckets in latest["coverage"].items():
        for bucket, counts in buckets.items():
            lines.append(
                f"| `{axis}` | `{bucket}` | {counts['passed']} | "
                f"{counts['failed']} | {counts['error']} | {counts['not_run']} |"
            )

    lines.extend(
        [
            "",
            "### Непрошедшие случаи",
            "",
            "| Case | Статус | Язык | Поведение | Режим | Capability | Диагностика | Время |",
            "|---|---|---|---|---|---|---|---:|",
        ]
    )
    latest_failures = [
        case for case in latest["cases"] if case["status"] not in {"passed", "skipped"}
    ]
    for case in latest_failures:
        lines.append(_case_table_row(case))
    if not latest_failures:
        lines.append("| — | Все случаи прошли | — | — | — | — | — | — |")

    lines.extend(
        [
            "",
            "## Все случаи по каждому прогону",
            "",
            "Разделы свёрнуты, чтобы страница оставалась читаемой. Внутри находятся",
            "все безопасные case ID и их фактические результаты из публичного JSON.",
            "",
        ]
    )
    for run in public_runs:
        outcome = run["outcome"]
        lines.extend(
            [
                "<details>",
                (
                    f"<summary><code>{run['run_id']}</code> — "
                    f"{outcome['counts']['passed']}/{outcome['planned']} passed"
                    "</summary>"
                ),
                "",
                "| Case | Статус | Язык | Поведение | Режим | Capability | Диагностика | Время |",
                "|---|---|---|---|---|---|---|---:|",
            ]
        )
        lines.extend(_case_table_row(case) for case in run["cases"])
        lines.extend(["", "</details>", ""])

    lines.extend(
        [
            "## Границы доказательств",
            "",
            "Таблица облегчает чтение, но не заменяет JSON. SHA-256 исходных локальных",
            "отчётов, точные агрегаты и полные структурированные поля находятся в",
            "[`index.json`](index.json) и файлах [`foundation/`](foundation/).",
            "",
        ]
    )
    return "\n".join(lines)


def _case_table_row(case: dict[str, Any]) -> str:
    context = case.get("context", {})
    diagnostic = case.get("diagnostic", {})
    evidence = diagnostic.get("evidence", {})
    diagnostic_text = evidence.get("code") or diagnostic.get("rule") or "—"
    duration = case.get("duration_ms")
    duration_text = f"{duration:.0f} ms" if duration is not None else "—"
    return (
        f"| `{case['id']}` | `{case['status']}` | "
        f"{_table_value(context.get('language'))} | "
        f"{_table_value(context.get('behavior'))} | "
        f"{_table_value(context.get('mode'))} | "
        f"{_table_value(context.get('capability'))} | "
        f"`{diagnostic_text}` | {duration_text} |"
    )


def _table_value(value: Any) -> str:
    return f"`{value}`" if value not in (None, "") else "—"


def _human_duration(seconds: float) -> str:
    rounded = int(round(seconds))
    hours, remainder = divmod(rounded, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {remaining_seconds:02d} s"
    return f"{remaining_seconds} s"


def build_public_run(run_directory: Path) -> dict[str, Any]:
    run_id = _safe_run_id(run_directory.name)
    manifest = _read_json(run_directory / "manifest.json")
    summary = _read_json(run_directory / "summary.json")
    failures = _read_json(run_directory / "failures.json")
    launch = _read_json(run_directory / "launch.json")
    progress = _read_json(run_directory / "progress.json")

    if _safe_run_id(str(summary.get("run_id"))) != run_id:
        raise UnsafeEvidence("summary run_id does not match its directory")
    fingerprint = _safe_sha256(manifest.get("source_fingerprint"))
    wall_time = max(
        0.0,
        float(progress.get("heartbeat_unix", 0.0))
        - float(launch.get("launched_unix", 0.0)),
    )

    failure_by_id = {
        _safe_token(item.get("id"), "failure.id"): item
        for item in failures.get("failures", [])
        if isinstance(item, dict)
    }
    cases = [
        _sanitize_case(item, failure_by_id)
        for item in summary.get("results", [])
        if isinstance(item, dict)
    ]
    counts = _sanitize_counts(summary.get("counts", {}))
    planned = _safe_nonnegative_int(summary.get("planned"), "planned")
    if sum(counts.values()) != planned:
        raise UnsafeEvidence("outcome counts do not add up to planned cases")
    if len(cases) != planned:
        raise UnsafeEvidence("case list does not match planned cases")

    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_kind": "ai-scenario-lab-foundation-run",
        "run_id": run_id,
        "provenance": {
            "source_fingerprint": fingerprint,
            "source_files_sha256": {
                name: _file_sha256(run_directory / name) for name in SOURCE_FILES
            },
            "automatic_retries": _safe_nonnegative_int(
                manifest.get("automatic_retries", 0), "automatic_retries"
            ),
        },
        "runtime": {
            "profile": _safe_token(manifest.get("profile"), "profile"),
            "model": _safe_token(manifest.get("model"), "model"),
            "context_tokens": _safe_nonnegative_int(
                manifest.get("context_tokens"), "context_tokens"
            ),
            "python": _python_version(manifest.get("python")),
            "platform": _platform_name(manifest.get("platform")),
            "seeds": [
                _safe_nonnegative_int(seed, "seed") for seed in manifest.get("seeds", [])
            ],
            "budget_seconds": _safe_nonnegative_int(
                manifest.get("budget_seconds"), "budget_seconds"
            ),
            "max_turn_ms": _safe_nonnegative_int(
                manifest.get("max_turn_ms"), "max_turn_ms"
            ),
        },
        "outcome": {
            "state": _safe_token(summary.get("state"), "state"),
            "verdict": _safe_token(summary.get("verdict"), "verdict"),
            "reason_code": _safe_optional_token(summary.get("reason"), "reason"),
            "planned": planned,
            "counts": counts,
            "wall_time_seconds": round(wall_time, 3),
        },
        "coverage": _sanitize_coverage(summary.get("coverage", {})),
        "slow_cases": _sanitize_slow_cases(summary.get("slow_cases")),
        "cases": cases,
    }


def _sanitize_case(
    item: dict[str, Any], failure_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    case_id = _safe_token(item.get("id"), "case.id")
    result: dict[str, Any] = {
        "id": case_id,
        "status": _safe_token(item.get("status"), "case.status"),
    }
    for field in ("tests", "exit_code"):
        if item.get(field) is not None:
            result[field] = int(item[field])
    if item.get("duration_ms") is not None:
        result["duration_ms"] = round(float(item["duration_ms"]), 3)
    if isinstance(item.get("skipped"), list):
        result["skipped_count"] = len(item["skipped"])
    if isinstance(item.get("failed_tests"), list):
        result["failed_tests"] = [
            _safe_token(value, "failed_test") for value in item["failed_tests"]
        ]
    context = item.get("context")
    if isinstance(context, dict):
        result["context"] = _sanitize_context(context)
    diagnostic = item.get("diagnostic")
    if not isinstance(diagnostic, dict):
        diagnostic = failure_by_id.get(case_id, {}).get("diagnostic")
    if isinstance(diagnostic, dict):
        result["diagnostic"] = _sanitize_diagnostic(diagnostic)
    if item.get("model") is not None:
        result["model"] = _safe_token(item["model"], "case.model")
    if item.get("ollama_version") is not None:
        result["ollama_version"] = _safe_token(
            item["ollama_version"], "case.ollama_version"
        )
    return result


def _sanitize_context(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in CONTEXT_FIELDS:
        item = value.get(field)
        if item is None:
            continue
        result[field] = (
            _safe_nonnegative_int(item, f"context.{field}")
            if field == "memory_depth"
            else _safe_token(item, f"context.{field}")
        )
    return result


def _sanitize_diagnostic(value: dict[str, Any]) -> dict[str, Any]:
    result = {
        "layer": _safe_token(value.get("layer"), "diagnostic.layer"),
        "fingerprint": _safe_token(
            value.get("fingerprint"), "diagnostic.fingerprint"
        ),
        "rule": _safe_token(value.get("rule"), "diagnostic.rule"),
    }
    evidence = value.get("evidence")
    if isinstance(evidence, dict):
        clean_evidence = {
            field: _safe_token(evidence[field], f"diagnostic.evidence.{field}")
            for field in EVIDENCE_FIELDS
            if evidence.get(field) is not None
        }
        if clean_evidence:
            result["evidence"] = clean_evidence
    return result


def _sanitize_coverage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UnsafeEvidence("coverage must be an object")
    result: dict[str, Any] = {}
    for axis, buckets in value.items():
        safe_axis = _safe_token(axis, "coverage.axis")
        if not isinstance(buckets, dict):
            raise UnsafeEvidence(f"coverage axis {safe_axis} must be an object")
        result[safe_axis] = {}
        for bucket, counts in buckets.items():
            safe_bucket = _safe_token(bucket, "coverage.bucket")
            result[safe_axis][safe_bucket] = _sanitize_counts(counts)
    return result


def _sanitize_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        raise UnsafeEvidence("counts must be an object")
    return {
        field: _safe_nonnegative_int(value.get(field, 0), f"counts.{field}")
        for field in COUNT_FIELDS
    }


def _sanitize_slow_cases(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    values = value if isinstance(value, list) else [value]
    return [_safe_token(item, "slow_case") for item in values]


def _platform_name(value: Any) -> str:
    token = _safe_token(value, "platform")
    return {"win32": "windows", "linux": "linux", "darwin": "macos"}.get(
        token, "other"
    )


def _python_version(value: Any) -> str:
    match = re.match(r"^(\d+\.\d+\.\d+)", str(value))
    if match is None:
        raise UnsafeEvidence("python version is not recognized")
    return match.group(1)


def _safe_run_id(value: Any) -> str:
    token = str(value)
    if RUN_ID_RE.fullmatch(token) is None:
        raise UnsafeEvidence("invalid run id")
    return token


def _safe_sha256(value: Any) -> str:
    token = str(value)
    if SHA256_RE.fullmatch(token) is None:
        raise UnsafeEvidence("invalid SHA-256 value")
    return token


def _safe_token(value: Any, field: str) -> str:
    token = str(value)
    if SAFE_TOKEN_RE.fullmatch(token) is None:
        raise UnsafeEvidence(f"unsafe {field}")
    return token


def _safe_optional_token(value: Any, field: str) -> str | None:
    if value in (None, ""):
        return None
    return _safe_token(value, field)


def _safe_nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise UnsafeEvidence(f"invalid {field}")
    number = int(value)
    if number < 0:
        raise UnsafeEvidence(f"negative {field}")
    return number


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise UnsafeEvidence(f"{path.name} must contain an object")
    return value


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
