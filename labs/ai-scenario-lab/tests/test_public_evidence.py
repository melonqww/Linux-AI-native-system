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
            "verdict": "backend_candidate",
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


def test_public_table_does_not_call_failed_full_run_green():
    run = _run("20260927T113342.838423Z", "foundation-v1", "case")
    run["outcome"]["counts"] = {
        "passed": 0, "failed": 1, "error": 0, "not_run": 0
    }
    run["outcome"]["verdict"] = "problems_found"
    run["cases"][0]["status"] = "failed"
    index = {"runs": [{
        "run_id": run["run_id"],
        "artifact": f"foundation/{run['run_id']}.json",
        "counts": run["outcome"]["counts"],
        "wall_time_seconds": 10,
        "verdict": "problems_found",
    }]}

    rendered = render_public_results(index, [run])

    assert "Verdict: `problems_found`" in rendered
    assert "Полный прогон выявил проблемы" in rendered
    assert "Зелёный результат" not in rendered


def test_public_table_shows_skips_inside_test_gates():
    run = _run("20260927T113342.838423Z", "foundation-v1", "live-case")
    run["cases"] = [
        {"id": "contracts", "status": "passed", "tests": 673, "skipped_count": 37},
        {"id": "lab-tests", "status": "passed", "tests": 146, "skipped_count": 0},
        {"id": "ollama-preflight", "status": "passed"},
        *run["cases"],
    ]
    run["outcome"]["planned"] = 4
    run["outcome"]["counts"]["passed"] = 4
    index = {"runs": [{
        "run_id": run["run_id"],
        "artifact": f"foundation/{run['run_id']}.json",
        "counts": run["outcome"]["counts"],
        "wall_time_seconds": 10,
        "verdict": "backend_candidate",
    }]}

    rendered = render_public_results(index, [run])

    assert "| `contracts` | `passed` | 673 | 37 |" in rendered
    assert "| `lab-tests` | `passed` | 146 | 0 |" in rendered
    assert "Пропуски внутри test gates не считаются" in rendered
