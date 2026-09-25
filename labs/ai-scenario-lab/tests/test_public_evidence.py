import pytest

from ai_scenario_lab.public_evidence import (
    UnsafeEvidence,
    _safe_token,
    render_public_results,
)


def _run(run_id, profile, subject):
    return {
        "run_id": run_id,
        "runtime": {
            "profile": profile,
            "platform": "windows",
            "model": "qwen3.5:2b",
            "context_tokens": 8192,
        },
        "outcome": {
            "state": "completed",
            "planned": 1,
            "counts": {"passed": 1, "failed": 0, "error": 0, "not_run": 0},
        },
        "coverage": {"mode": {subject: {
            "passed": 1, "failed": 0, "error": 0, "not_run": 0,
        }}},
        "cases": [{"id": subject, "status": "passed"}],
    }


def test_public_table_distinguishes_focused_run_from_last_full_run():
    full_id = "20260925T081344.764208Z"
    focused_id = "20260925T090000.000000Z"
    runs = [
        _run(full_id, "foundation-v1", "full-case"),
        _run(focused_id, "foundation-failed-subjects-v1", "focused-case"),
    ]
    index = {"runs": [
        {
            "run_id": run["run_id"],
            "artifact": f"foundation/{run['run_id']}.json",
            "counts": run["outcome"]["counts"],
            "wall_time_seconds": 10,
            "verdict": "backend_candidate",
        }
        for run in runs
    ]}

    rendered = render_public_results(index, runs)

    assert "| Run | Профиль |" in rendered
    assert "`foundation-failed-subjects-v1`" in rendered
    latest = rendered.split("## Последний полный прогон — ", 1)[1]
    assert latest.startswith(f"`{full_id}`")
    assert "| `mode` | `full-case` |" in latest.split("## Все случаи", 1)[0]


def test_public_token_rejects_paths_and_prose():
    for value in ("C:\\Users\\name", "/home/name/file.pdf", "my private text"):
        with pytest.raises(UnsafeEvidence):
            _safe_token(value, "test")
