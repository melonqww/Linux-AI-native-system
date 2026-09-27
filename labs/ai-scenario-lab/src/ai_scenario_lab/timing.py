"""Numeric-only, lab-owned timing observations around production boundaries."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from threading import RLock
from time import perf_counter
from typing import TypeVar

from ai_native_workspace import WorkspaceStore


_T = TypeVar("_T")


class LabTimings:
    def __init__(self) -> None:
        self._lock = RLock()
        self._spans: list[tuple[str, float, float]] = []
        self._stages: dict[str, list[tuple[str, float]]] = defaultdict(list)

    def call(self, name: str, callback: Callable[[], _T]) -> _T:
        started = perf_counter()
        try:
            return callback()
        finally:
            ended = perf_counter()
            with self._lock:
                self._spans.append((name, started, ended))

    def stage(self, run_id: str, name: str) -> None:
        with self._lock:
            self._stages[run_id].append((name, perf_counter()))

    def snapshot(self, run_id: str, *, started: float, ended: float) -> dict[str, object]:
        """Report one turn, including an approval continuation of an earlier run.

        Components can overlap their parent workspace stages. Stage totals are
        disjoint; pre-submit/polling time is intentionally left unattributed.
        """
        with self._lock:
            spans = tuple(self._spans)
            stages = tuple(self._stages.get(run_id, ()))
        components: dict[str, float] = defaultdict(float)
        for name, begin, finish in spans:
            if started <= begin and finish <= ended:
                components[name] += (finish - begin) * 1_000
        stage_totals: dict[str, float] = defaultdict(float)
        previous_name = None
        previous_time = started
        for name, timestamp in stages:
            if timestamp <= started:
                previous_name = name
                continue
            if timestamp > ended:
                break
            if previous_name is not None:
                stage_totals[previous_name] += (timestamp - previous_time) * 1_000
            previous_name = name
            previous_time = timestamp
        if previous_name is not None:
            stage_totals[previous_name] += (ended - previous_time) * 1_000
        elapsed = max(0.0, (ended - started) * 1_000)
        attributed = min(elapsed, sum(stage_totals.values()))
        return {
            "elapsed_ms": round(elapsed, 3),
            "stages_ms": {key: round(value, 3) for key, value in stage_totals.items()},
            "components_ms": {key: round(value, 3) for key, value in components.items()},
            "unattributed_ms": round(elapsed - attributed, 3),
        }


class TimingWorkspaceStore(WorkspaceStore):
    """Observe committed transitions without altering the store contract."""

    def __init__(self, database, timings: LabTimings) -> None:
        super().__init__(database)
        self.timings = timings

    def create_run(self, user_message_id, *, run_id=None):
        run = super().create_run(user_message_id, run_id=run_id)
        self.timings.stage(run.run_id, run.stage.value)
        return run

    def transition(self, run_id, stage):
        run = super().transition(run_id, stage)
        self.timings.stage(run.run_id, run.stage.value)
        return run

    def awaiting_approval(self, run_id, *, task_id, approval_request_id):
        run = super().awaiting_approval(
            run_id, task_id=task_id, approval_request_id=approval_request_id
        )
        self.timings.stage(run.run_id, run.stage.value)
        return run

    def finish(self, run_id, stage, assistant_message_id, *, task_id=None):
        run = super().finish(run_id, stage, assistant_message_id, task_id=task_id)
        self.timings.stage(run.run_id, run.stage.value)
        return run


class TimingCapabilityRouter:
    def __init__(self, router: object, timings: LabTimings) -> None:
        self.router = router
        self.timings = timings

    def candidates(self, text):
        return self.timings.call("capability_candidates", lambda: self.router.candidates(text))

    def requested_operations(self, text):
        return self.timings.call(
            "requested_operations", lambda: self.router.requested_operations(text)
        )

    def required_operations(self, text):
        return self.timings.call(
            "required_operations", lambda: self.router.required_operations(text)
        )

    def available_operations(self):
        return self.router.available_operations()


class TimingEmbeddingProvider:
    def __init__(self, provider: object, timings: LabTimings, role: str) -> None:
        self.provider = provider
        self.timings = timings
        self.role = role

    def embed(self, texts):
        return self.timings.call(
            f"embedding_{self.role}", lambda: self.provider.embed(texts)
        )


def model_usage(events: tuple[dict[str, object], ...]) -> dict[str, object]:
    """Only timings and counts; never model prompts or output text."""
    calls = []
    totals = defaultdict(float)
    for event in events:
        usage = event.get("ollama_usage", ())
        if not isinstance(usage, (tuple, list)):
            continue
        for item in usage:
            if not isinstance(item, dict):
                continue
            call = {
                "kind": event.get("kind"),
                "status": item.get("status", "ok"),
            }
            for key in (
                "wall_ms", "total_ms", "load_ms", "prompt_eval_ms", "eval_ms",
                "prompt_tokens", "cached_prompt_tokens", "output_tokens",
            ):
                value = item.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                    call[key] = value
                    if key.endswith("_ms"):
                        totals[key] += value
            calls.append(call)
    return {
        "calls": calls,
        "totals_ms": {key: round(value, 3) for key, value in totals.items()},
    }
