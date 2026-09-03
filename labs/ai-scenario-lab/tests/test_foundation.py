import gc
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import ai_scenario_lab.foundation as foundation
import ai_scenario_lab.foundation_worker as worker
from ai_scenario_lab.campaign import _journey_cases
from ai_scenario_lab.adaptive_journeys import load_journey
from ai_scenario_lab.cli import parser
from ai_scenario_lab.foundation_cases import foundation_scenarios
from ai_scenario_lab.scenario import load_scenario


LAB = Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_path():
    # Keep temporary files within the same lab-owned boundary as other tests.
    root = LAB / ".runtime" / "foundation-tests" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        assert root.resolve().is_relative_to((LAB / ".runtime").resolve())
        gc.collect()  # SQLite handles held by production object cycles on Windows.
        shutil.rmtree(root)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    lab = tmp_path / "lab"
    for folder in ("scenarios", "journeys", "fixtures"):
        shutil.copytree(LAB / folder, lab / folder)
    monkeypatch.setattr(foundation, "source_fingerprint", lambda _: "stable")
    run = foundation.prepare(lab, tmp_path)
    return lab, tmp_path, run


def reduce_plan(run, kinds=("scenario", "scenario")):
    manifest = foundation.read_json(run / "manifest.json")
    manifest["cases"] = [
        {"id": f"case-{index}", "kind": kind, "timeout_seconds": 20}
        for index, kind in enumerate(kinds)
    ]
    foundation.atomic_json(run / "manifest.json", manifest)
    foundation.publish(run, manifest, "prepared")
    return manifest


def test_prepare_never_calls_model_or_starts_process(prepared, monkeypatch):
    lab, project, _ = prepared
    monkeypatch.setattr(
        foundation.subprocess, "Popen", lambda *_a, **_k: pytest.fail("must not launch")
    )
    run = foundation.prepare(lab, project)
    manifest = foundation.read_json(run / "manifest.json")
    assert (
        len(manifest["cases"]) == 91
    )  # 3 gates + 2 * (23 contracts + 21 journeys)
    assert len({case["id"] for case in manifest["cases"]}) == 91
    assert manifest["model"] == "qwen3.5:2b"
    assert manifest["context_tokens"] == 8192
    assert manifest["automatic_retries"] == 0
    assert manifest["seeds"] == [7, 19]
    assert foundation.status(run)["state"] == "prepared"
    assert foundation.read_json(run / "summary.json")["verdict"] == "incomplete"
    assert foundation.resolve_run(lab, None) == run.resolve()
    foundation.verify_inputs(run, manifest)
    assert {case["language"] for case in manifest["cases"] if "language" in case} == {
        "ru",
        "en",
        "mixed",
    }
    for seed in manifest["seeds"]:
        assert {
            case["behavior"] for case in manifest["cases"] if case.get("seed") == seed
        } >= {
            "standard",
            "typo",
            "slang",
            "no_punctuation",
            "verbose",
            "cautious",
            "impatient",
        }


def test_snapshot_is_replayable_and_detects_tampering(prepared):
    lab, _, run = prepared
    manifest = foundation.read_json(run / "manifest.json")
    source = lab / "scenarios/01-chat-memory.json"
    source.write_text("{}", encoding="utf-8")
    foundation.verify_inputs(run, manifest)
    snapshot = run / "inputs/scenarios/01-chat-memory.json"
    snapshot.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot changed"):
        foundation.verify_inputs(run, manifest)


@pytest.mark.parametrize("seeds", [(7,), (7, 7), (True, 19), (-1, 19)])
def test_invalid_repeat_matrix_rejected(tmp_path, seeds):
    with pytest.raises(ValueError):
        foundation.prepare(tmp_path, tmp_path, seeds=seeds)


def test_generated_memory_checks_are_real_5_and_35_turn_sessions(tmp_path):
    cases = foundation_scenarios()
    assert len(cases) == 7
    for payload in cases:
        path = tmp_path / f"{payload['id']}.json"
        foundation.atomic_json(path, payload)
        spec = load_scenario(path)
        if "memory-" in spec.scenario_id:
            assert len(spec.turns) == int(spec.scenario_id.rsplit("-", 1)[-1])
            assert all(turn.expect["no_operations"] for turn in spec.turns)
            assert spec.turns[-1].expect["assistant_contains_any"]


def test_small_journey_budget_does_not_exclude_english():
    journeys = tuple(
        load_journey(path) for path in sorted((LAB / "journeys").glob("*.json"))
    )
    cases = _journey_cases(journeys, persona_set="all", max_cases=3, seed=7)
    assert len({case.journey.journey_id for case in cases}) == 3
    assert {case.persona.language.value for case in cases} == {"ru", "en"}


@pytest.mark.parametrize("first", ["failed", "timeout", "error"])
def test_supervisor_continues_after_failure_without_hiding_it(
    prepared, monkeypatch, first
):
    lab, project, run = prepared
    reduce_plan(run)
    calls = []

    def process(command, **kwargs):
        identifier = command[-1]
        calls.append(identifier)
        kwargs["heartbeat"]()
        if identifier == "case-0" and first in {"timeout", "error"}:
            return {"status": first, "duration_ms": 10}
        foundation.atomic_json(
            run / "results" / f"{identifier}.json",
            {"id": identifier, "status": first if identifier == "case-0" else "passed"},
        )
        return {"status": "passed"}

    monkeypatch.setattr(foundation, "run_process", process)
    assert foundation.supervise(lab, project, run) == 1
    summary = foundation.read_json(run / "summary.json")
    assert summary["state"] == "completed"
    assert summary["verdict"] == "problems_found"
    assert summary["counts"]["passed"] == 1
    assert len(calls) == 2
    assert len(foundation.read_json(run / "failures.json")["failures"]) == 1
    with pytest.raises(ValueError, match="already started"):
        foundation.supervise(lab, project, run)


def test_missing_worker_result_is_never_a_pass(prepared, monkeypatch):
    lab, project, run = prepared
    reduce_plan(run)
    monkeypatch.setattr(
        foundation, "run_process", lambda *_a, **_k: {"status": "passed"}
    )
    foundation.supervise(lab, project, run)
    assert foundation.read_json(run / "summary.json")["counts"] == {"error": 2}


def test_prerequisite_failure_blocks_live_cases(prepared, monkeypatch):
    lab, project, run = prepared
    reduce_plan(run, ("preflight", "scenario"))
    monkeypatch.setattr(
        foundation, "run_process", lambda *_a, **_k: {"status": "error"}
    )
    foundation.supervise(lab, project, run)
    summary = foundation.read_json(run / "summary.json")
    assert summary["state"] == "blocked"
    assert summary["verdict"] == "incomplete"
    assert summary["counts"]["not_run"] == 1


def test_source_changes_block_a_prepared_run(prepared, monkeypatch):
    lab, project, run = prepared
    monkeypatch.setattr(foundation, "source_fingerprint", lambda _: "changed")
    monkeypatch.setattr(
        foundation, "run_process", lambda *_a, **_k: pytest.fail("must not run")
    )
    foundation.supervise(lab, project, run)
    assert foundation.status(run)["state"] == "interrupted"
    assert "source changed" in foundation.status(run)["reason"]


def test_global_deadline_leaves_remaining_cases_not_run(prepared, monkeypatch):
    lab, project, run = prepared
    manifest = reduce_plan(run)
    manifest["budget_seconds"] = 1
    foundation.atomic_json(run / "manifest.json", manifest)
    ticks = iter([0, 2])
    monkeypatch.setattr(foundation.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(
        foundation, "run_process", lambda *_a, **_k: pytest.fail("deadline")
    )
    foundation.supervise(lab, project, run)
    summary = foundation.read_json(run / "summary.json")
    assert summary["reason"] == "campaign_deadline"
    assert summary["counts"] == {"not_run": 2}


def test_containment_breach_stops_further_execution(prepared, monkeypatch):
    lab, project, run = prepared
    reduce_plan(run)

    def process(command, **kwargs):
        foundation.atomic_json(
            run / "results/case-0.json",
            {
                "id": "case-0",
                "status": "failed",
                "diagnostic": {"layer": "CONTAINMENT"},
            },
        )
        return {"status": "passed"}

    monkeypatch.setattr(foundation, "run_process", process)
    foundation.supervise(lab, project, run)
    summary = foundation.read_json(run / "summary.json")
    assert summary["reason"] == "containment_breach"
    assert summary["counts"]["not_run"] == 1


def test_gate_distinguishes_speed_from_function_and_never_releases_beta(prepared):
    _, _, run = prepared
    manifest = reduce_plan(run)
    for case in manifest["cases"]:
        foundation.atomic_json(
            run / "results" / f"{case['id']}.json",
            {"id": case["id"], "status": "passed"},
        )
    summary = foundation.publish(run, manifest, "completed")
    assert summary["verdict"] == "backend_candidate"
    assert summary["beta_0_1_released"] is False
    foundation.atomic_json(
        run / "results/case-0.json",
        {"id": "case-0", "status": "passed", "slow_turns": [1]},
    )
    assert (
        foundation.publish(run, manifest, "completed")["verdict"]
        == "performance_problems"
    )
    assert foundation.publish(run, manifest, "interrupted")["verdict"] == "incomplete"


def test_stale_heartbeat_does_not_look_like_a_live_run(prepared):
    _, _, run = prepared
    foundation.atomic_json(
        run / "progress.json", {"state": "running", "heartbeat_unix": time.time() - 200}
    )
    assert foundation.status(run)["state"] == "interrupted_or_unresponsive"


def test_real_process_timeout_and_crash_are_bounded(tmp_path):
    started = time.monotonic()
    result = foundation.run_process(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=tmp_path,
        log=tmp_path / "hang.log",
        timeout=0.2,
    )
    assert result["status"] == "timeout"
    assert time.monotonic() - started < 12
    result = foundation.run_process(
        [sys.executable, "-c", "raise RuntimeError('test crash')"],
        cwd=tmp_path,
        log=tmp_path / "crash.log",
        timeout=10,
    )
    assert result["status"] == "error"
    assert "test crash" in (tmp_path / "crash.log").read_text()


def test_lock_is_exclusive_and_released(prepared):
    lab, _, _ = prepared
    with foundation.campaign_lock(lab):
        with pytest.raises(OSError):
            with foundation.campaign_lock(lab):
                pytest.fail("second owner")
    with foundation.campaign_lock(lab):
        pass


def test_background_launch_is_detached_without_shell(prepared, monkeypatch):
    lab, _, run = prepared
    captured = {}

    def popen(command, **kwargs):
        captured.update(command=command, **kwargs)
        return SimpleNamespace(pid=12345)

    monkeypatch.setattr(foundation.subprocess, "Popen", popen)
    assert foundation.start_background(lab, run) == 12345
    assert "shell" not in captured
    assert "foundation" in captured["command"]
    assert captured.get("creationflags") or captured.get("start_new_session")
    assert captured["stdin"] == subprocess.DEVNULL


def test_cli_prepare_is_not_start():
    arguments = parser().parse_args(["foundation", "prepare"])
    assert arguments.action == "prepare"
    assert arguments.seeds == [7, 19]
    assert arguments.budget_seconds == 14400


def test_worker_captures_missing_model_as_evidence(prepared, monkeypatch):
    lab, project, run = prepared
    monkeypatch.setattr(
        worker,
        "model_identity",
        lambda _: (_ for _ in ()).throw(ConnectionError("offline")),
    )
    assert worker.run_case(lab, project, run, "ollama-preflight") == 0
    result = foundation.read_json(run / "results/ollama-preflight.json")
    assert result["status"] == "error"
    assert "offline" in result["reason"]


def test_model_identity_requires_exact_tag_and_digest(monkeypatch):
    from io import BytesIO

    responses = iter(
        [
            {"models": [{"name": "qwen3.5:2b", "digest": "sha256:test"}]},
            {"version": "test"},
        ]
    )
    monkeypatch.setattr(
        worker.urllib.request,
        "urlopen",
        lambda *_a, **_k: BytesIO(json.dumps(next(responses)).encode()),
    )
    assert (
        worker.model_identity(
            {"model": "qwen3.5:2b", "base_url": "http://127.0.0.1:11434"}
        )["digest"]
        == "sha256:test"
    )
    monkeypatch.setattr(
        worker.urllib.request,
        "urlopen",
        lambda *_a, **_k: BytesIO(b'{"models": [{"name": "qwen3:1.7b"}]}'),
    )
    with pytest.raises(ValueError, match="exact model"):
        worker.model_identity(
            {"model": "qwen3.5:2b", "base_url": "http://127.0.0.1:11434"}
        )


def test_corrupt_result_keeps_report_readable_and_is_not_a_pass(prepared):
    _, _, run = prepared
    manifest = reduce_plan(run)
    (run / "results").mkdir()
    (run / "results/case-0.json").write_text('{"status":', encoding="utf-8")
    summary = foundation.publish(run, manifest, "completed")
    assert summary["counts"] == {"error": 1, "not_run": 1}
    assert summary["verdict"] == "incomplete"
    assert (
        "invalid_result"
        in foundation.read_json(run / "failures.json")["failures"][0]["reason"]
    )


def test_failed_background_start_is_not_left_as_prepared(prepared):
    _, _, run = prepared
    foundation.atomic_json(
        run / "launch.json", {"pid": 123, "launched_unix": time.time() - 60}
    )
    assert foundation.status(run)["state"] == "startup_unconfirmed"


def test_real_detached_supervisor_finishes_without_caller_polling_workers(prepared):
    lab, project, run = prepared
    reduce_plan(run)
    # A tiny test-only worker avoids Ollama; the actual supervisor, process
    # isolation, detachment, atomic publication and reporting all run for real.
    driver = (
        "import sys\n"
        f"sys.path[:] = {sys.path!r}\n"
        "from pathlib import Path\n"
        "from ai_scenario_lab import foundation as f\n"
        f"run = Path({str(run)!r})\n"
        "if sys.argv[1] == 'foundation-worker':\n"
        "    ident = sys.argv[-1]\n"
        "    f.atomic_json(run / 'results' / (ident + '.json'), {'id': ident, 'status': 'passed'})\n"
        "else:\n"
        "    f.source_fingerprint = lambda _: 'stable'\n"
        f"    raise SystemExit(f.supervise(Path({str(lab)!r}), Path({str(project)!r}), run))\n"
    )
    (lab / "run.py").write_text(driver, encoding="utf-8")
    foundation.start_background(lab, run)
    # Two workers have 20 seconds each. Allow their full budget plus supervisor
    # startup, including when this test itself runs inside a campaign worker.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if foundation.status(run)["state"] in {"completed", "blocked", "interrupted", "incomplete"}:
            break
        time.sleep(0.05)
    assert foundation.status(run)["state"] == "completed", json.dumps(foundation.status(run)) + (run / "supervisor.log").read_text(encoding="utf-8")
    assert foundation.read_json(run / "summary.json")["verdict"] == "backend_candidate"


def test_worker_runs_production_search_with_deterministic_model_and_partial_trace(
    prepared, monkeypatch
):
    from test_runner import DeterministicProvider
    from ai_scenario_lab.runner import ScenarioRunner

    lab, _, run = prepared
    identity = {"model": "qwen3.5:2b", "digest": "test", "ollama_version": "test"}
    foundation.atomic_json(run / "model.json", identity)
    monkeypatch.setattr(worker, "model_identity", lambda _: identity)
    monkeypatch.setattr(
        worker,
        "ScenarioRunner",
        lambda **kwargs: ScenarioRunner(
            **kwargs, provider_factory=DeterministicProvider
        ),
    )
    identifier = "s7--search-pdf"
    assert worker.run_case(lab, LAB.parents[1], run, identifier) == 0
    result = foundation.read_json(run / "results" / f"{identifier}.json")
    assert result["status"] == "passed", result
    trace = foundation.read_json(run / result["trace"])
    assert trace["outcome"]["containment"]["passed"]
    assert (run / "partial" / identifier / "turn-1.json").is_file()


@pytest.mark.parametrize(
    "xml,code",
    [
        ("<testsuites><testsuite/></testsuites>", 0),
        (
            '<testsuites><testsuite><testcase name="only"><skipped/></testcase></testsuite></testsuites>',
            0,
        ),
        (
            '<testsuites><testsuite><testcase name="bad"><failure/></testcase></testsuite></testsuites>',
            1,
        ),
    ],
)
def test_empty_skipped_or_failed_unit_suite_cannot_pass(
    prepared, monkeypatch, xml, code
):
    lab, project, run = prepared
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k nothing")
    monkeypatch.setenv("AI_NATIVE_RUN_OLLAMA_EVALS", "1")

    def invoke(command, **kwargs):
        assert "PYTEST_ADDOPTS" not in kwargs["env"]
        assert kwargs["env"]["AI_NATIVE_RUN_OLLAMA_EVALS"] == "0"
        (run / "contracts.xml").write_text(xml, encoding="utf-8")
        return SimpleNamespace(returncode=code)

    monkeypatch.setattr(worker.subprocess, "run", invoke)
    assert worker.run_case(lab, project, run, "contracts") == 0
    assert foundation.read_json(run / "results/contracts.json")["status"] == "failed"
