from pathlib import Path

from ai_scenario_lab.scenario import discover_scenarios, load_scenario


LAB_ROOT = Path(__file__).resolve().parents[1]


def test_all_committed_scenarios_are_strict_and_discoverable():
    scenarios = discover_scenarios(LAB_ROOT / "scenarios")
    assert len(scenarios) >= 7
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
