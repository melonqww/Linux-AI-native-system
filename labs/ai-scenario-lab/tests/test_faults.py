import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from ai_scenario_lab.containment import ContainmentGuard
from ai_scenario_lab.contracts import FaultEffect, FaultSpec
from ai_scenario_lab.faults import FaultController, InjectedLabFailure


LAB_ROOT = Path(__file__).resolve().parents[1]


def test_fault_controller_triggers_only_the_selected_occurrence():
    controller = FaultController(
        (FaultSpec("model.route", 2, FaultEffect.RAISE),)
    )

    assert controller.call("model.route", lambda: "first") == "first"
    with pytest.raises(InjectedLabFailure, match="model.route"):
        controller.call("model.route", lambda: "second")
    assert controller.call("model.route", lambda: "third") == "third"
    assert controller.events == [
        {
            "point": "model.route",
            "occurrence": 2,
            "effect": "raise",
            "delay_ms": 0,
        }
    ]


def test_malformed_fault_is_confined_to_classification():
    controller = FaultController(
        (FaultSpec("model.classify_turn", 1, FaultEffect.MALFORMED),)
    )
    assert controller.call("model.classify_turn", lambda: {"valid": True}) == {}


def test_containment_guard_detects_a_changed_canary():
    root = LAB_ROOT / ".runtime" / "guard-tests" / str(uuid4())
    try:
        protected = root / "protected.txt"
        protected.parent.mkdir(parents=True)
        protected.write_text("unchanged", encoding="utf-8")
        guard = ContainmentGuard(
            run_parent=root / "run",
            scenario_id="sample",
            protected_paths=(protected,),
        )
        assert guard.verify()["passed"] is True

        guard.sentinel.write_text("tampered", encoding="utf-8")
        result = guard.verify()
        assert result["passed"] is False
        assert str(guard.sentinel) in result["changed"]
    finally:
        shutil.rmtree(root, ignore_errors=True)
