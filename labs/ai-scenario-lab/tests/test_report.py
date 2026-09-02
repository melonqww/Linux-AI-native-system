import json
import shutil
from pathlib import Path
from uuid import uuid4

from ai_scenario_lab.contracts import CheckResult, ScenarioOutcome, TurnOutcome
from ai_scenario_lab.report import assess_stability, write_report


def test_report_preserves_machine_and_human_readable_results():
    lab_root = Path(__file__).resolve().parents[1]
    root = lab_root / ".runtime" / "report-tests" / str(uuid4())
    try:
        outcome = ScenarioOutcome(
            "sample",
            "Sample scenario",
            False,
            (),
            ({"kind": "route", "status": "error"},),
            (),
            str(root / "virtual-pc"),
            "model timeout",
        )
        directory = write_report(
            root / "reports", (outcome,), run_id="test-run", model="qwen3.5:2b"
        )
        payload = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
        markdown = (directory / "summary.md").read_text(encoding="utf-8")
        assert payload["failed"] == 1
        assert payload["schema_version"] == 2
        assert payload["model"] == "qwen3.5:2b"
        assert "FAIL — sample" in markdown
        assert "model timeout" in markdown
    finally:
        if root.resolve().is_relative_to((lab_root / ".runtime").resolve()):
            shutil.rmtree(root, ignore_errors=True)


def test_safety_stability_stays_strict_and_coverage_is_reported():
    lab_root = Path(__file__).resolve().parents[1]
    root = lab_root / ".runtime" / "report-tests" / str(uuid4())
    turn = TurnOutcome(
        1,
        "copy",
        "cancelled",
        (
            CheckResult(
                "capabilities",
                True,
                ["storage.materialize.plan-copy"],
                ["storage.materialize.plan-copy"],
            ),
        ),
        (),
        (),
        None,
        12.5,
        1,
    )
    outcomes = (
        ScenarioOutcome(
            "deny",
            "Denied",
            True,
            (turn,),
            (),
            (),
            "virtual",
            tags=("denial", "safety"),
            duration_ms=20,
            containment={"passed": True},
        ),
        ScenarioOutcome(
            "deny",
            "Denied",
            False,
            (turn,),
            (),
            (),
            "virtual",
            tags=("denial", "safety"),
            duration_ms=30,
            containment={"passed": True},
        ),
    )
    stability = assess_stability(outcomes, min_pass_rate=0.5)
    assert stability[0]["required_rate"] == 1.0
    assert stability[0]["stable"] is False

    try:
        report = write_report(
            root, outcomes, run_id="coverage", model="test", min_pass_rate=0.5
        )
        payload = json.loads((report / "summary.json").read_text(encoding="utf-8"))
        row = payload["capability_coverage"][0]
        assert row["capability"] == "storage.materialize.plan-copy"
        assert row["denial"] == {"covered": True, "passing": False}
    finally:
        shutil.rmtree(root, ignore_errors=True)
