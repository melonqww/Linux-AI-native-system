"""Lab timing is numeric, bounded to one turn, and independent of live Ollama."""

import gc
import pytest
import shutil
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from ai_scenario_lab import timing
from ai_scenario_lab.environment import MeasuredOllamaModelProvider, _ollama_action_phase
from ai_native_intents.ollama import OllamaModelProvider
from ai_native_workspace import MessageKind, MessageRole, WorkspaceStage


def test_action_phase_labels_only_known_wire_shapes():
    assert _ollama_action_phase({"tools": []}) == "action_proposal"
    assert _ollama_action_phase({"format": {"properties": {"choice": {"type": "string"}}}}) == "conditional_argument_review"
    assert _ollama_action_phase({"format": {"properties": {"reviews": {"type": "array"}}}}) == "preserved_argument_review"
    assert _ollama_action_phase({"format": {"properties": {"choice": {"type": "integer"}}}}) is None
    assert _ollama_action_phase(None) is None


def test_stage_and_component_timings_do_not_double_count(monkeypatch):
    ticks = iter((1.0, 2.0, 3.0, 5.0, 6.0, 9.0))
    monkeypatch.setattr(timing, "perf_counter", lambda: next(ticks))
    measured = timing.LabTimings()
    measured.stage("run", "received")
    measured.stage("run", "understanding")
    assert measured.call("model_classify_turn", lambda: "ok") == "ok"
    measured.stage("run", "planning")
    measured.stage("run", "completed")
    snapshot = measured.snapshot("run", started=0.0, ended=10.0)
    assert snapshot == {
        "elapsed_ms": 10000.0,
        "stages_ms": {
            "received": 1000.0,
            "understanding": 4000.0,
            "planning": 3000.0,
            "completed": 1000.0,
        },
        "components_ms": {"model_classify_turn": 2000.0},
        "unattributed_ms": 1000.0,
    }


def test_approval_continuation_starts_from_existing_stage(monkeypatch):
    ticks = iter((1.0, 2.0, 4.0))
    monkeypatch.setattr(timing, "perf_counter", lambda: next(ticks))
    measured = timing.LabTimings()
    measured.stage("run", "executing")
    measured.stage("run", "awaiting_approval")
    measured.stage("run", "completed")
    snapshot = measured.snapshot("run", started=3.0, ended=5.0)
    assert snapshot["stages_ms"] == {
        "awaiting_approval": 1000.0,
        "completed": 1000.0,
    }


def test_workspace_store_observer_preserves_transitions():
    lab_root = Path(__file__).resolve().parents[1]
    root = lab_root / ".runtime" / "timing-tests" / uuid4().hex
    root.mkdir(parents=True)
    try:
        measured = timing.LabTimings()
        store = timing.TimingWorkspaceStore(root / "workspace.sqlite3", measured)
        started = perf_counter()
        user = store.append_message(MessageRole.USER, MessageKind.CONVERSATION, "hello")
        run = store.create_run(user.message_id)
        store.transition(run.run_id, WorkspaceStage.UNDERSTANDING)
        answer = store.append_message(
            MessageRole.ASSISTANT, MessageKind.CONVERSATION, "hi"
        )
        completed = store.complete(run.run_id, answer.message_id)
        assert completed.stage is WorkspaceStage.COMPLETED
        snapshot = measured.snapshot(run.run_id, started=started, ended=perf_counter())
        assert set(snapshot["stages_ms"]) == {
            "received", "understanding", "completed"
        }
        assert snapshot["unattributed_ms"] >= 0
    finally:
        assert root.resolve().is_relative_to((lab_root / ".runtime").resolve())
        del store
        gc.collect()
        shutil.rmtree(root)


def test_ollama_usage_summary_contains_only_numeric_metrics():
    usage = timing.model_usage(({
        "kind": "respond_chat",
        "request": {"user_text": "private prompt"},
        "ollama_usage": [{
            "status": "ok",
            "wall_ms": 20.0,
            "load_ms": 4.0,
            "prompt_eval_ms": 5.0,
            "eval_ms": 8.0,
            "prompt_tokens": 12,
            "output_tokens": 3,
            "response_message": {"content": "private answer"},
        }],
    },))
    assert usage["totals_ms"] == {
        "wall_ms": 20.0,
        "load_ms": 4.0,
        "prompt_eval_ms": 5.0,
        "eval_ms": 8.0,
    }
    assert usage["calls"][0]["kind"] == "respond_chat"
    assert "private" not in str(usage)


def test_measured_provider_records_success_and_failure(monkeypatch):
    provider = MeasuredOllamaModelProvider()

    def success(_self, _method, _path, _payload=None, *, timeout=None):
        return {
            "message": {"content": "private"},
            "total_duration": 12_000_000,
            "load_duration": 2_000_000,
            "prompt_eval_duration": 3_000_000,
            "eval_duration": 5_000_000,
            "prompt_eval_count": 10,
            "prompt_eval_cached_count": 4,
            "eval_count": 6,
        }

    monkeypatch.setattr(OllamaModelProvider, "_json_request", success)
    provider._json_request("POST", "/api/chat", {})
    item = provider.drain_usage()[0]
    assert item["load_ms"] == 2.0
    assert item["prompt_eval_ms"] == 3.0
    assert item["eval_ms"] == 5.0
    assert item["cached_prompt_tokens"] == 4

    def failure(_self, _method, _path, _payload=None, *, timeout=None):
        raise TimeoutError("private details")

    monkeypatch.setattr(OllamaModelProvider, "_json_request", failure)
    with pytest.raises(TimeoutError):
        provider._json_request("POST", "/api/chat", {})
    item = provider.drain_usage()[0]
    assert item["status"] == "error"
    assert item["error_type"] == "TimeoutError"
    assert "private" not in str(item)
