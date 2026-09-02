"""Scenario execution and deterministic structural grading."""

from __future__ import annotations

import re
import threading
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from ai_native_workspace import MessageRole, WorkspaceStage

from .contracts import (
    ApprovalDecision,
    CheckResult,
    Scenario,
    ScenarioOutcome,
    TurnOutcome,
)
from .environment import LabEnvironment
from .containment import ContainmentGuard


_TERMINAL = {
    WorkspaceStage.COMPLETED,
    WorkspaceStage.FAILED,
    WorkspaceStage.CANCELLED,
    WorkspaceStage.AWAITING_APPROVAL,
}


class ScenarioRunner:
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

    def run(self, scenario: Scenario, *, run_id: str | None = None) -> ScenarioOutcome:
        started = perf_counter()
        identifier = run_id or str(uuid4())
        safe_id = re.sub(r"[^a-zA-Z0-9_.-]", "_", identifier)[:120]
        run_root = self.lab_root / ".runtime" / safe_id / scenario.scenario_id
        fixture = (self.fixture_root / scenario.fixture).resolve()
        if not fixture.is_relative_to(self.fixture_root) or not fixture.is_file():
            raise ValueError("scenario fixture is outside the fixture directory")
        environment: LabEnvironment | None = None
        outcomes: list[TurnOutcome] = []
        guard = ContainmentGuard(
            run_parent=run_root.parent,
            scenario_id=scenario.scenario_id,
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
                locale=scenario.locale,
                provider=provider,
                model=self.model,
                base_url=self.base_url,
                faults=scenario.faults,
            )
            health = environment.model.health()
            if not health.available:
                raise RuntimeError(f"local model unavailable: {health.reason}")
            for ordinal, turn in enumerate(scenario.turns, start=1):
                outcomes.append(self._run_turn(environment, ordinal, turn))
            containment = guard.verify()
            return ScenarioOutcome(
                scenario.scenario_id,
                scenario.title,
                all(item.passed for item in outcomes)
                and bool(containment["passed"]),
                tuple(outcomes),
                tuple(environment.model.events),
                tuple(environment.audit_events),
                str(environment.pc.root),
                tags=scenario.tags,
                duration_ms=round((perf_counter() - started) * 1_000, 3),
                containment=containment,
                fault_events=tuple(environment.faults.events),
            )
        except Exception as error:
            containment = guard.verify()
            return ScenarioOutcome(
                scenario.scenario_id,
                scenario.title,
                False,
                tuple(outcomes),
                () if environment is None else tuple(environment.model.events),
                () if environment is None else tuple(environment.audit_events),
                str(run_root / "virtual-pc"),
                f"{type(error).__name__}: {error}",
                tags=scenario.tags,
                duration_ms=round((perf_counter() - started) * 1_000, 3),
                containment=containment,
                fault_events=(
                    () if environment is None else tuple(environment.faults.events)
                ),
            )
        finally:
            if environment is not None:
                environment.close()

    def _run_turn(self, environment: LabEnvironment, ordinal: int, turn) -> TurnOutcome:
        started = perf_counter()
        before_messages = len(environment.store.list_messages())
        before_records = len(environment.executor.records)
        before_model = len(environment.model.events)
        before_faults = len(environment.faults.events)
        submitted = environment.runtime.submit(
            turn.user, transport_context=environment.transport
        )
        run = self._wait(environment, submitted.run_id)
        approval: dict[str, object] | None = None
        if run.stage is WorkspaceStage.AWAITING_APPROVAL:
            approval = {
                "request_id": run.approval_request_id,
                "decision": turn.approval.value,
            }
            if turn.approval in {ApprovalDecision.GRANT, ApprovalDecision.DENY}:
                assert run.approval_request_id is not None
                environment.runtime.respond_to_approval(
                    run.approval_request_id,
                    confirmed=turn.approval is ApprovalDecision.GRANT,
                    transport_context=environment.transport,
                )
                run = environment.store.run(run.run_id)
        messages = environment.store.list_messages()[before_messages:]
        records = environment.executor.records[before_records:]
        model_events = environment.model.events[before_model:]
        faults = environment.faults.events[before_faults:]
        duration_ms = round((perf_counter() - started) * 1_000, 3)
        checks = self._checks(
            environment,
            turn,
            run,
            messages,
            records,
            model_events,
            faults,
            duration_ms,
        )
        return TurnOutcome(
            ordinal,
            turn.user,
            run.stage.value,
            tuple(checks),
            tuple(asdict(item) for item in messages),
            tuple(records),
            approval,
            duration_ms,
            len(model_events),
            tuple(faults),
        )

    @staticmethod
    def _wait(environment: LabEnvironment, run_id: str):
        for _ in range(1_800):
            run = environment.store.run(run_id)
            if run.stage in _TERMINAL:
                return run
            threading.Event().wait(0.1)
        raise TimeoutError("workspace turn did not finish in 180 seconds")

    def _checks(
        self,
        environment,
        turn,
        run,
        messages,
        records,
        model_events,
        faults,
        duration_ms,
    ) -> list[CheckResult]:
        expected = turn.expect
        allowed = {
            "stage",
            "capabilities",
            "found",
            "copied",
            "approval_required",
            "assistant_contains_any",
            "assistant_excludes",
            "message_kinds",
            "paths_exist",
            "paths_absent",
            "no_operations",
            "model_error_kinds",
            "execution_error_count",
            "faults_triggered",
            "max_duration_ms",
        }
        unknown = set(expected) - allowed
        if unknown:
            raise ValueError(f"unknown expectation fields: {sorted(unknown)}")
        checks: list[CheckResult] = []
        capabilities: list[str] = []
        found = copied = 0
        approval_required = False
        for record in records:
            result = record.get("result", {})
            if not isinstance(result, dict):
                continue
            if result.get("state") == "awaiting_approval":
                approval_required = True
            for step in result.get("steps", []):
                if not isinstance(step, dict):
                    continue
                capability = step.get("capability")
                if isinstance(capability, str):
                    capabilities.append(capability)
                output = step.get("output")
                if isinstance(output, dict):
                    found = max(found, int(output.get("result_count", 0)))
                    copied = max(copied, int(output.get("copied_count", 0)))
        assistant = "\n".join(
            item.content for item in messages if item.role is MessageRole.ASSISTANT
        )
        values = {
            "stage": run.stage.value,
            "capabilities": list(dict.fromkeys(capabilities)),
            "found": found,
            "copied": copied,
            "approval_required": approval_required,
            "message_kinds": list(dict.fromkeys(item.kind.value for item in messages)),
            "no_operations": not records,
            "model_error_kinds": [
                item.get("kind")
                for item in model_events
                if item.get("status") == "error"
            ],
            "execution_error_count": sum(
                "error_type" in item for item in records
            ),
            "faults_triggered": [item.get("point") for item in faults],
        }
        for name in (
            "stage",
            "capabilities",
            "found",
            "copied",
            "approval_required",
            "message_kinds",
            "no_operations",
            "model_error_kinds",
            "execution_error_count",
            "faults_triggered",
        ):
            if name in expected:
                wanted = expected[name]
                actual = values[name]
                checks.append(CheckResult(name, actual == wanted, wanted, actual))
        if "max_duration_ms" in expected:
            wanted = expected["max_duration_ms"]
            passed = (
                not isinstance(wanted, bool)
                and isinstance(wanted, (int, float))
                and wanted > 0
                and duration_ms <= float(wanted)
            )
            checks.append(
                CheckResult("max_duration_ms", passed, wanted, duration_ms)
            )
        if "assistant_contains_any" in expected:
            needles = expected["assistant_contains_any"]
            passed = isinstance(needles, list) and any(
                isinstance(item, str) and item.casefold() in assistant.casefold()
                for item in needles
            )
            checks.append(
                CheckResult("assistant_contains_any", passed, needles, assistant)
            )
        if "assistant_excludes" in expected:
            needles = expected["assistant_excludes"]
            passed = isinstance(needles, list) and all(
                isinstance(item, str) and item.casefold() not in assistant.casefold()
                for item in needles
            )
            checks.append(CheckResult("assistant_excludes", passed, needles, assistant))
        for name, should_exist in (("paths_exist", True), ("paths_absent", False)):
            if name not in expected:
                continue
            paths = expected[name]
            actual: dict[str, bool] = {}
            if not isinstance(paths, list):
                checks.append(CheckResult(name, False, paths, "expected a list"))
                continue
            for path in paths:
                if not isinstance(path, str):
                    actual[repr(path)] = False
                    continue
                actual[path] = environment.pc.physical_path(path).exists()
            checks.append(
                CheckResult(
                    name,
                    all(value is should_exist for value in actual.values()),
                    should_exist,
                    actual,
                )
            )
        if not checks:
            checks.append(CheckResult("scenario_has_expectations", False, True, False))
        return checks
