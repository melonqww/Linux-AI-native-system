from pathlib import Path

from ai_scenario_lab.scenario import discover_scenarios, load_scenario


LAB_ROOT = Path(__file__).resolve().parents[1]


def test_all_committed_scenarios_are_strict_and_discoverable():
    scenarios = discover_scenarios(LAB_ROOT / "scenarios")
    assert len(scenarios) >= 14
    assert len({item.scenario_id for item in scenarios}) == len(scenarios)
    assert all(item.turns for item in scenarios)
    assert all((LAB_ROOT / "fixtures" / item.fixture).is_file() for item in scenarios)


def test_smoke_suite_is_a_real_subset():
    all_scenarios = discover_scenarios(LAB_ROOT / "scenarios")
    smoke = discover_scenarios(LAB_ROOT / "scenarios", suite="smoke")
    assert 1 < len(smoke) < len(all_scenarios)
    assert all("smoke" in item.tags for item in smoke)


def test_denial_scenario_explicitly_checks_no_filesystem_effect():
    scenario = load_scenario(LAB_ROOT / "scenarios" / "05-copy-denied.json")
    denied = scenario.turns[-1]
    assert denied.approval.value == "deny"
    assert denied.expect["stage"] == "cancelled"
    assert denied.expect["paths_absent"]


def test_fault_scenarios_use_only_valid_lab_boundaries():
    scenarios = discover_scenarios(LAB_ROOT / "scenarios")
    faulted = [item for item in scenarios if item.faults]
    assert len(faulted) >= 4
    assert all("fault" in item.tags for item in faulted)
    assert {
        fault.point for scenario in faulted for fault in scenario.faults
    } >= {"model.classify_turn", "model.route", "executor.execute"}


def test_prompt_injection_fixture_is_data_on_an_enrolled_disk():
    scenario = load_scenario(
        LAB_ROOT / "scenarios" / "09-prompt-injection-document.json"
    )
    fixture = (LAB_ROOT / "fixtures" / scenario.fixture).read_text(encoding="utf-8")
    assert "safetycanary" in fixture
    assert "prompt-injection.txt" in fixture
