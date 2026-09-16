import json
import shutil
from pathlib import Path
from uuid import uuid4

from ai_scenario_lab.security_campaign import SecurityCampaignRunner


def test_security_campaign_exercises_production_module_and_writes_report():
    lab_root = Path(__file__).resolve().parents[1]
    project_root = lab_root.parents[1]
    run_id = f"test-{uuid4()}"
    report = lab_root / "reports" / "security" / run_id
    try:
        directory, results = SecurityCampaignRunner(project_root, lab_root).run(
            run_id=run_id
        )
        payload = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
        assert payload["campaign"] == "security-center"
        assert payload["containment_passed"] is True
        assert payload["total"] == len(results) == 10
        assert payload["passed"] + payload["skipped"] == payload["total"]
        assert payload["failed"] == 0
        assert len(payload["source_fingerprint"]) == 64
        assert all(item.passed or item.skipped for item in results)
        assert "Security Campaign report" in (directory / "summary.md").read_text(
            encoding="utf-8"
        )
    finally:
        shutil.rmtree(report, ignore_errors=True)


def test_security_fixture_is_safe_synthetic_text():
    lab_root = Path(__file__).resolve().parents[1]
    payload = json.loads(
        (lab_root / "security-fixtures" / "manifest.json").read_text(encoding="utf-8")
    )
    assert payload["schema_version"] == 1
    assert "SYNTHETIC_MARKER" in payload["synthetic_threat"]
    assert "X5O!P%@AP" not in json.dumps(payload)
