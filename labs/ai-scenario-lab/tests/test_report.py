import json
import shutil
from pathlib import Path
from uuid import uuid4

from ai_scenario_lab.contracts import ScenarioOutcome
from ai_scenario_lab.report import write_report


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
        assert payload["model"] == "qwen3.5:2b"
        assert "FAIL — sample" in markdown
        assert "model timeout" in markdown
    finally:
        if root.resolve().is_relative_to((lab_root / ".runtime").resolve()):
            shutil.rmtree(root, ignore_errors=True)
