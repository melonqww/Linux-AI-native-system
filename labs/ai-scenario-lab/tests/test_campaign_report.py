import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from ai_scenario_lab.campaign_report import CampaignAttempt, write_campaign_report
from ai_scenario_lab.diagnostics import DiagnosticContext, classify_problem


def _context(language="ru", depth=5, failure_kind="timeout"):
    return DiagnosticContext(
        language=language,
        behavior="ordinary",
        mode="action",
        memory_depth=depth,
        capability="storage.search",
        decision="none",
        failure_kind=failure_kind,
        input_modality="text",
    )


def _failed(attempt_id, context, evidence, trace=None):
    return CampaignAttempt(
        attempt_id,
        "search-files",
        "journey",
        context,
        False,
        15.5,
        classify_problem(context, evidence),
        trace,
    )


def test_campaign_report_writes_coverage_failure_groups_and_trace_links():
    lab_root = Path(__file__).resolve().parents[1]
    reports = lab_root / ".runtime" / "campaign-report-tests" / str(uuid4())
    ru = _context()
    en = _context("en", 35)
    attempts = (
        _failed("attempt-1", ru, {"code": "model_timeout"}, "traces/attempt 1.json"),
        _failed("attempt-2", ru, {"code": "model_timeout"}, "traces/attempt-2.json"),
        CampaignAttempt("attempt-3", "search-files", "scenario", en, True, 10),
    )
    try:
        directory = write_campaign_report(reports, attempts, run_id="campaign-1")
        payload = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
        summary_md = (directory / "summary.md").read_text(encoding="utf-8")
        failures = (directory / "failures.md").read_text(encoding="utf-8")
        assert payload["passed"] == 1
        assert payload["failed"] == 2
        assert payload["failure_groups"][0]["count"] == 2
        assert payload["unknown_failures"] == []
        assert "language × memory_depth" in payload["coverage"]["intersections"]
        assert "### language × memory_depth" in summary_md
        assert "### mode × capability" in summary_md
        assert "### decision × failure_kind" in summary_md
        assert "[trace](traces/attempt%201.json)" in failures
    finally:
        shutil.rmtree(reports, ignore_errors=True)


def test_unknown_failures_are_not_grouped_under_a_shared_fingerprint():
    lab_root = Path(__file__).resolve().parents[1]
    reports = lab_root / ".runtime" / "campaign-report-tests" / str(uuid4())
    context = _context(failure_kind="novel")
    attempts = (
        _failed("unknown-diagnostic", context, {"code": "brand_new"}),
        CampaignAttempt("no-diagnostic", "chat", "scenario", context, False, 1),
    )
    try:
        directory = write_campaign_report(reports, attempts, run_id="unknowns")
        payload = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
        assert payload["failure_groups"] == []
        assert [item["attempt_id"] for item in payload["unknown_failures"]] == [
            "unknown-diagnostic",
            "no-diagnostic",
        ]
        assert "## UNKNOWN" in (directory / "failures.md").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(reports, ignore_errors=True)


def test_validation_happens_before_report_directory_is_created():
    lab_root = Path(__file__).resolve().parents[1]
    root = lab_root / ".runtime" / "campaign-report-tests" / str(uuid4())
    root.mkdir(parents=True)
    context = _context()
    duplicate = CampaignAttempt("same", "chat", "scenario", context, True, 1)
    try:
        with pytest.raises(ValueError, match="unique"):
            write_campaign_report(root, (duplicate, duplicate), run_id="bad-run")
        assert list(root.iterdir()) == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_attempt_rejects_inconsistent_or_non_finite_data():
    context = _context()
    diagnostic = classify_problem(context, {"code": "model_timeout"})
    with pytest.raises(ValueError, match="passing"):
        CampaignAttempt("one", "chat", "scenario", context, True, 1, diagnostic)
    with pytest.raises(ValueError, match="finite"):
        CampaignAttempt("two", "chat", "scenario", context, False, float("nan"))
    with pytest.raises(ValueError, match="kind"):
        CampaignAttempt("three", "chat", "contract", context, False, 1)


def test_existing_campaign_is_never_overwritten():
    lab_root = Path(__file__).resolve().parents[1]
    root = lab_root / ".runtime" / "campaign-report-tests" / str(uuid4())
    context = _context()
    attempt = CampaignAttempt("one", "chat", "scenario", context, True, 1)
    try:
        write_campaign_report(root, (attempt,), run_id="same")
        with pytest.raises(FileExistsError):
            write_campaign_report(root, (attempt,), run_id="same")
    finally:
        shutil.rmtree(root, ignore_errors=True)
