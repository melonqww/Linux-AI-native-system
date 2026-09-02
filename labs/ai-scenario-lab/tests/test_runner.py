import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from ai_native_intents import ModelTurn, ModelTurnKind

from ai_scenario_lab.contracts import (
    ApprovalDecision,
    FaultEffect,
    FaultSpec,
    Scenario,
    ScenarioTurn,
)
from ai_scenario_lab.runner import ScenarioRunner


LAB_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = LAB_ROOT.parents[1]


class DeterministicProvider:
    def health(self):
        return SimpleNamespace(
            available=True, reason=None, model="fake", version="test"
        )

    def respond_chat(self, request):
        if request.history:
            return f"Помню предыдущее сообщение: {request.history[-1].content}"
        return "Привет! Я работаю внутри тестовой среды."

    def classify_turn(self, request):
        return {
            "kind": "action",
            "language": "ru",
            "confidence": 0.99,
            "conversation_text": None,
            "action_text": request.user_text,
        }

    def route(self, request):
        text = request.user_text.casefold()
        if "скопируй" in text:
            return ModelTurn(
                ModelTurnKind.ACTION,
                response_text="Подготовлю копирование после подтверждения.",
                intent_payload={
                    "schema_version": 1,
                    "language": "ru",
                    "summary": request.user_text,
                    "confidence": 0.99,
                    "operations": [
                        {
                            "id": "op_copy",
                            "kind": "copy_results",
                            "arguments": {
                                "results_from": "context.active_results",
                                "destination": "desktop",
                                "directory_name": "Проверка",
                            },
                            "depends_on": [],
                            "evidence": [request.user_text],
                        }
                    ],
                },
            )
        return ModelTurn(
            ModelTurnKind.ACTION,
            response_text="Проверю виртуальные диски.",
            intent_payload={
                "schema_version": 1,
                "language": "ru",
                "summary": request.user_text,
                "confidence": 0.99,
                "operations": [
                    {
                        "id": "op_search",
                        "kind": "search_documents",
                        "arguments": {"mode": "metadata", "extensions": ["pdf"]},
                        "depends_on": [],
                        "evidence": [request.user_text],
                    }
                ],
            },
        )

    def summarize_result(self, facts, *, locale):
        return "unused"

    def compose_conversation(self, request, *, system_result):
        return None


def runner():
    return ScenarioRunner(
        project_root=PROJECT_ROOT,
        lab_root=LAB_ROOT,
        fixture_root=LAB_ROOT / "fixtures",
        provider_factory=DeterministicProvider,
    )


def cleanup(run_id: str):
    target = (LAB_ROOT / ".runtime" / run_id).resolve()
    if target.is_relative_to((LAB_ROOT / ".runtime").resolve()):
        shutil.rmtree(target, ignore_errors=True)


def test_runner_executes_real_search_and_approved_copy_inside_virtual_pc():
    run_id = f"unit-{uuid4()}"
    scenario = Scenario(
        "approved-test",
        "approved",
        "ru",
        ("unit",),
        "base-desktop.json",
        (
            ScenarioTurn(
                "Найди все PDF-файлы",
                expect={
                    "stage": "completed",
                    "capabilities": ["documents.query.search"],
                    "found": 3,
                },
            ),
            ScenarioTurn(
                "Скопируй их в папку Проверка на рабочем столе",
                ApprovalDecision.GRANT,
                {
                    "stage": "completed",
                    "capabilities": ["storage.materialize.plan-copy"],
                    "copied": 3,
                    "approval_required": True,
                    "paths_exist": [
                        "/home/test-user/Desktop/Проверка/algebra.pdf",
                        "/home/test-user/Desktop/Проверка/broken.pdf",
                        "/home/test-user/Desktop/Проверка/geometry.pdf",
                    ],
                },
            ),
        ),
        LAB_ROOT / "tests" / "generated",
    )
    try:
        outcome = runner().run(scenario, run_id=run_id)
        assert outcome.passed, outcome
        assert len(outcome.audit_events) > 0
        assert all(
            Path(outcome.virtual_pc).resolve().is_relative_to(LAB_ROOT.resolve())
            for _ in [0]
        )
    finally:
        cleanup(run_id)


def test_runner_denial_cancels_without_creating_destination():
    run_id = f"unit-{uuid4()}"
    scenario = Scenario(
        "denied-test",
        "denied",
        "ru",
        ("unit",),
        "base-desktop.json",
        (
            ScenarioTurn(
                "Найди все PDF-файлы",
                expect={"stage": "completed", "found": 3},
            ),
            ScenarioTurn(
                "Скопируй их в папку Проверка на рабочем столе",
                ApprovalDecision.DENY,
                {
                    "stage": "cancelled",
                    "copied": 0,
                    "approval_required": True,
                    "paths_absent": ["/home/test-user/Desktop/Проверка"],
                },
            ),
        ),
        LAB_ROOT / "tests" / "generated",
    )
    try:
        outcome = runner().run(scenario, run_id=run_id)
        assert outcome.passed, outcome
    finally:
        cleanup(run_id)


def test_runner_contains_an_injected_executor_failure():
    run_id = f"unit-{uuid4()}"
    scenario = Scenario(
        "fault-test",
        "fault",
        "ru",
        ("unit", "fault", "safety"),
        "base-desktop.json",
        (
            ScenarioTurn(
                "Найди все PDF-файлы",
                expect={
                    "stage": "failed",
                    "execution_error_count": 1,
                    "faults_triggered": ["executor.execute"],
                },
            ),
        ),
        LAB_ROOT / "tests" / "generated",
        (FaultSpec("executor.execute", 1, FaultEffect.RAISE),),
    )
    try:
        outcome = runner().run(scenario, run_id=run_id)
        assert outcome.passed, outcome
        assert outcome.containment["passed"] is True
        assert outcome.fault_events[0]["point"] == "executor.execute"
    finally:
        cleanup(run_id)
