"""Execution adapter from adaptive journeys to the real workspace runtime."""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from ai_native_workspace import MessageKind, MessageRole, WorkspaceStage

from ai_scenario_lab.containment import ContainmentGuard
from ai_scenario_lab.environment import LabEnvironment

from .contracts import (
    ActionKind,
    JourneyAction,
    JourneySpec,
    Observation,
    ObservedEffect,
)
from .engine import JourneySession, StopReason
from .evaluation import DeterministicEvaluation, evaluate_deterministically


_WAITABLE_TERMINAL = {
    WorkspaceStage.COMPLETED,
    WorkspaceStage.FAILED,
    WorkspaceStage.CANCELLED,
    WorkspaceStage.AWAITING_APPROVAL,
}


@dataclass(frozen=True)
class JourneyTurnOutcome:
    ordinal: int
    state_id: str
    action: JourneyAction
    observation: Observation
    duration_ms: float


@dataclass(frozen=True)
class JourneyOutcome:
    journey_id: str
    title: str
    passed: bool
    turns: tuple[JourneyTurnOutcome, ...]
    stop_reason: StopReason | None
    evaluation: DeterministicEvaluation | None
    effects: tuple[ObservedEffect, ...]
    containment: dict[str, object]
    virtual_pc: str
    duration_ms: float
    capabilities: tuple[str, ...] = ()
    model_events: tuple[dict[str, object], ...] = ()
    audit_events: tuple[dict[str, object], ...] = ()
    execution_records: tuple[dict[str, object], ...] = ()
    error: str | None = None


class WorkspaceObservationAdapter:
    """Projects workspace state onto the narrow, user-visible observation API."""

    @staticmethod
    def adapt(run, messages, records) -> Observation:
        visible_messages = [
            {"role": item.role.value, "text": item.content}
            for item in messages
            if item.role in {MessageRole.ASSISTANT, MessageRole.SYSTEM}
        ]
        status = run.stage.value
        if run.stage is WorkspaceStage.COMPLETED and any(
            item.kind is MessageKind.CLARIFICATION for item in messages
        ):
            status = "unsupported"
        results: dict[str, object] = {}
        for record in records:
            payload = record.get("result")
            if not isinstance(payload, dict):
                continue
            for step in payload.get("steps", []):
                if not isinstance(step, dict):
                    continue
                output = step.get("output")
                if not isinstance(output, dict):
                    continue
                count = output.get("result_count")
                if isinstance(count, int) and not isinstance(count, bool):
                    results["search_hits"] = max(
                        int(results.get("search_hits", 0)), count
                    )
                count = output.get("copied_count")
                if isinstance(count, int) and not isinstance(count, bool):
                    results["copied_count"] = max(
                        int(results.get("copied_count", 0)), count
                    )
        if run.stage is WorkspaceStage.CANCELLED:
            results["cancelled"] = True
        approval = None
        if run.stage is WorkspaceStage.AWAITING_APPROVAL:
            prompt = next(
                (
                    item.content
                    for item in reversed(messages)
                    if item.role is MessageRole.ASSISTANT
                ),
                "Confirmation is required to continue.",
            )
            approval = {"prompt": prompt, "action_label": "operation"}
        return Observation.from_public_payload(
            {
                "status": status,
                "messages": visible_messages,
                "approval": approval,
                "results": [
                    {"key": key, "value": value} for key, value in results.items()
                ],
            }
        )


class TrustedEffectCollector:
    """Derives effects from the virtual filesystem, not model/executor claims."""

    def __init__(self, environment: LabEnvironment) -> None:
        self.environment = environment
        self.before_files, self.before_directories = self._snapshot()

    def collect(self, containment: dict[str, object]) -> tuple[ObservedEffect, ...]:
        after_files, after_directories = self._snapshot()
        effects: list[ObservedEffect] = []
        known_digests = set(self.before_files.values())
        for target in sorted(after_directories - self.before_directories):
            effects.append(ObservedEffect("directory_create", target))
        for target in sorted(set(after_files) - set(self.before_files)):
            digest = after_files[target]
            kind = "file_copy" if digest in known_digests else "file_write"
            effects.append(ObservedEffect(kind, target, {"sha256": digest}))
        for target in sorted(set(self.before_files) - set(after_files)):
            effects.append(ObservedEffect("file_delete", target))
        for target in sorted(set(self.before_files) & set(after_files)):
            if self.before_files[target] != after_files[target]:
                effects.append(
                    ObservedEffect(
                        "file_modify", target, {"sha256": after_files[target]}
                    )
                )
        if not bool(containment.get("passed")):
            for target in containment.get("changed", []):
                effects.append(ObservedEffect("path_escape", str(target)))
        return tuple(effects)

    def _snapshot(self) -> tuple[dict[str, str], set[str]]:
        files: dict[str, str] = {}
        directories: set[str] = set()
        for volume in self.environment.pc.volumes:
            for path in volume.physical_root.rglob("*"):
                if path.is_dir():
                    directories.add(self.environment.pc.virtual_path(path))
                elif path.is_file():
                    files[self.environment.pc.virtual_path(path)] = self._digest(path)
        return files, directories

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


class JourneyRunner:
    def __init__(
        self,
        *,
        project_root: Path,
        lab_root: Path,
        fixture_root: Path,
        model: str = "qwen3.5:2b",
        base_url: str = "http://127.0.0.1:11434",
        provider_factory=None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.lab_root = lab_root.resolve()
        self.fixture_root = fixture_root.resolve()
        self.model = model
        self.base_url = base_url
        self.provider_factory = provider_factory

    def run(self, spec: JourneySpec, *, run_id: str | None = None) -> JourneyOutcome:
        started = perf_counter()
        identifier = run_id or str(uuid4())
        safe_id = re.sub(r"[^a-zA-Z0-9_.-]", "_", identifier)[:120]
        run_root = self.lab_root / ".runtime" / safe_id / spec.journey_id
        fixture = (self.fixture_root / spec.fixture).resolve()
        if not fixture.is_relative_to(self.fixture_root) or not fixture.is_file():
            raise ValueError("journey fixture is outside the fixture directory")
        environment: LabEnvironment | None = None
        collector: TrustedEffectCollector | None = None
        turns: list[JourneyTurnOutcome] = []
        evaluation = None
        effects: tuple[ObservedEffect, ...] = ()
        stop_reason = None
        final_observation: Observation | None = None
        guard = ContainmentGuard(
            run_parent=run_root.parent,
            scenario_id=spec.journey_id,
            protected_paths=(
                self.lab_root / "README.md",
                self.project_root / "README.md",
            ),
        )
        try:
            provider = self.provider_factory() if self.provider_factory else None
            environment = LabEnvironment(
                project_root=self.project_root,
                lab_root=self.lab_root,
                run_root=run_root,
                fixture_path=fixture,
                locale=spec.dimensions.language,
                provider=provider,
                model=self.model,
                base_url=self.base_url,
            )
            health = environment.model.health()
            if not health.available:
                raise RuntimeError(f"local model unavailable: {health.reason}")
            collector = TrustedEffectCollector(environment)
            session = JourneySession(spec)
            decision = session.start()
            pending_run = None
            while not decision.stopped:
                assert decision.action is not None
                turn_started = perf_counter()
                before_messages = len(environment.store.list_messages())
                before_records = len(environment.executor.records)
                if decision.action.kind in {
                    ActionKind.FOLLOW_UP,
                    ActionKind.CORRECT,
                    ActionKind.REPHRASE,
                }:
                    assert decision.action.text is not None
                    submitted = environment.runtime.submit(
                        decision.action.text, transport_context=environment.transport
                    )
                    workspace_run = self._wait(environment, submitted.run_id)
                elif decision.action.kind in {ActionKind.APPROVE, ActionKind.DENY}:
                    if pending_run is None or pending_run.approval_request_id is None:
                        raise RuntimeError(
                            "journey approval action has no pending request"
                        )
                    environment.runtime.respond_to_approval(
                        pending_run.approval_request_id,
                        confirmed=decision.action.kind is ActionKind.APPROVE,
                        transport_context=environment.transport,
                    )
                    workspace_run = environment.store.run(pending_run.run_id)
                else:
                    raise RuntimeError("unexpected emitted journey action")
                messages = environment.store.list_messages()[before_messages:]
                records = environment.executor.records[before_records:]
                final_observation = WorkspaceObservationAdapter.adapt(
                    workspace_run, messages, records
                )
                turns.append(
                    JourneyTurnOutcome(
                        len(turns) + 1,
                        decision.state_id,
                        decision.action,
                        final_observation,
                        round((perf_counter() - turn_started) * 1_000, 3),
                    )
                )
                pending_run = (
                    workspace_run
                    if workspace_run.stage is WorkspaceStage.AWAITING_APPROVAL
                    else None
                )
                decision = session.react(final_observation)
            stop_reason = decision.reason
            containment = guard.verify()
            effects = collector.collect(containment)
            if final_observation is None:
                raise RuntimeError("journey produced no observation")
            evaluation = evaluate_deterministically(
                spec.goal, final_observation, effects
            )
            passed = (
                evaluation.passed
                and bool(containment["passed"])
                and stop_reason in {StopReason.TERMINAL_STATUS, StopReason.POLICY_STOP}
            )
            records = tuple(environment.executor.records)
            return JourneyOutcome(
                journey_id=spec.journey_id,
                title=spec.title,
                passed=passed,
                turns=tuple(turns),
                stop_reason=stop_reason,
                evaluation=evaluation,
                effects=effects,
                containment=containment,
                virtual_pc=str(environment.pc.root),
                duration_ms=round((perf_counter() - started) * 1_000, 3),
                capabilities=_recorded_capabilities(records),
                model_events=tuple(environment.model.events),
                audit_events=tuple(environment.audit_events),
                execution_records=records,
            )
        except Exception as error:
            containment = guard.verify()
            if collector is not None:
                effects = collector.collect(containment)
            records = () if environment is None else tuple(environment.executor.records)
            return JourneyOutcome(
                journey_id=spec.journey_id,
                title=spec.title,
                passed=False,
                turns=tuple(turns),
                stop_reason=stop_reason,
                evaluation=evaluation,
                effects=effects,
                containment=containment,
                virtual_pc=str(run_root / "virtual-pc"),
                duration_ms=round((perf_counter() - started) * 1_000, 3),
                capabilities=_recorded_capabilities(records),
                model_events=(
                    () if environment is None else tuple(environment.model.events)
                ),
                audit_events=(
                    () if environment is None else tuple(environment.audit_events)
                ),
                execution_records=records,
                error=f"{type(error).__name__}: {error}",
            )
        finally:
            if environment is not None:
                environment.close()

    @staticmethod
    def _wait(environment: LabEnvironment, run_id: str):
        for _ in range(1_800):
            run = environment.store.run(run_id)
            if run.stage in _WAITABLE_TERMINAL:
                return run
            threading.Event().wait(0.1)
        raise TimeoutError("workspace journey turn did not finish in 180 seconds")


def _recorded_capabilities(
    records: tuple[dict[str, object], ...],
) -> tuple[str, ...]:
    capabilities: list[str] = []
    for record in records:
        for container in (record.get("plan"), record.get("result")):
            if not isinstance(container, dict):
                continue
            for step in container.get("steps", []):
                if not isinstance(step, dict):
                    continue
                capability = step.get("capability")
                if isinstance(capability, str):
                    capabilities.append(capability)
    return tuple(dict.fromkeys(capabilities))
