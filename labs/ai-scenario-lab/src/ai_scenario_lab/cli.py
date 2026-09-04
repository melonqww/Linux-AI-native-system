from __future__ import annotations

import argparse
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ai_native_intents import OllamaModelProvider

from .campaign import CampaignRunner
from .report import assess_stability, write_report
from .runner import ScenarioRunner
from .scenario import discover_scenarios


LAB_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = LAB_ROOT.parents[1]
DEFAULT_MODEL = "qwen3.5:2b"
DEFAULT_URL = "http://127.0.0.1:11434"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="ai-lab")
    sub = result.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser(
        "prepare", help="check Ollama and optionally pull the exact model"
    )
    prepare.add_argument("--pull", action="store_true")
    prepare.add_argument("--model", default=DEFAULT_MODEL)
    prepare.add_argument("--base-url", default=DEFAULT_URL)
    run = sub.add_parser("run", help="run isolated real-model scenarios")
    run.add_argument("suite", nargs="?", default="smoke")
    run.add_argument("--scenario")
    run.add_argument("--repeat", type=int, default=1)
    run.add_argument(
        "--min-pass-rate",
        type=float,
        default=1.0,
        help="required repeat pass rate; safety scenarios always require 1.0",
    )
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--base-url", default=DEFAULT_URL)
    report = sub.add_parser("report", help="print the latest Markdown report")
    report.add_argument(
        "--path", action="store_true", help="print only the report path"
    )
    campaign = sub.add_parser(
        "campaign", help="run contract scenarios and adaptive user journeys"
    )
    campaign.add_argument("suite", nargs="?", default="full")
    campaign.add_argument("--repeat", type=int, default=1)
    campaign.add_argument(
        "--persona-set", choices=("standard", "all"), default="standard"
    )
    campaign.add_argument("--max-journey-cases", type=int, default=24)
    campaign.add_argument("--seed", type=int, default=0)
    campaign.add_argument("--model", default=DEFAULT_MODEL)
    campaign.add_argument("--base-url", default=DEFAULT_URL)
    campaign.add_argument(
        "--journey",
        action="append",
        default=[],
        help="run only this journey id; may be repeated",
    )
    campaign.add_argument(
        "--skip-scenarios",
        action="store_true",
        help="skip contract scenarios and run selected journeys only",
    )
    foundation = sub.add_parser(
        "foundation", help="prepare/start/inspect a durable foundation campaign"
    )
    foundation.add_argument("action", choices=("prepare", "start", "run", "status"))
    foundation.add_argument(
        "--run", help="prepared run directory; defaults to reports/latest.json"
    )
    foundation.add_argument("--seeds", type=int, nargs="+", default=[7, 19])
    foundation.add_argument("--budget-seconds", type=int, default=14400)
    worker = sub.add_parser("foundation-worker", help=argparse.SUPPRESS)
    worker.add_argument("--run", required=True)
    worker.add_argument("--case", required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.command == "prepare":
        return _prepare(arguments.model, arguments.base_url, pull=arguments.pull)
    if arguments.command == "run":
        return _run(arguments)
    if arguments.command == "campaign":
        return _campaign(arguments)
    if arguments.command == "foundation":
        return _foundation(arguments)
    if arguments.command == "foundation-worker":
        from .foundation import resolve_run
        from .foundation_worker import run_case

        return run_case(
            LAB_ROOT, PROJECT_ROOT, resolve_run(LAB_ROOT, arguments.run), arguments.case
        )
    return _report(path_only=arguments.path)


def _foundation(arguments) -> int:
    import json
    from .foundation import prepare, resolve_run, start_background, status, supervise

    if arguments.action == "prepare":
        run = prepare(
            LAB_ROOT,
            PROJECT_ROOT,
            seeds=tuple(arguments.seeds),
            budget_seconds=arguments.budget_seconds,
        )
        print(f"PREPARED (not started): {run}")
        print(f"REPORT: {run / 'summary.md'}")
        return 0
    run = resolve_run(LAB_ROOT, arguments.run)
    if arguments.action == "start":
        pid = start_background(LAB_ROOT, run)
        print(
            f"DISPATCHED supervisor PID {pid}; use foundation status to verify startup."
        )
        print(f"REPORT: {run / 'summary.md'}")
        return 0
    if arguments.action == "status":
        print(json.dumps(status(run), ensure_ascii=False, indent=2))
        return 0
    return supervise(LAB_ROOT, PROJECT_ROOT, run)


def _campaign(arguments) -> int:
    if not 1 <= arguments.repeat <= 20:
        raise SystemExit("--repeat must be between 1 and 20")
    if not 1 <= arguments.max_journey_cases <= 10_000:
        raise SystemExit("--max-journey-cases must be between 1 and 10000")
    health = OllamaModelProvider(
        model=arguments.model, base_url=arguments.base_url
    ).health()
    if not health.available:
        print(f"NOT READY: {health.reason}")
        print("Run `python run.py prepare --pull` before a campaign.")
        return 2
    report, attempts = CampaignRunner(
        project_root=PROJECT_ROOT,
        lab_root=LAB_ROOT,
        model=arguments.model,
        base_url=arguments.base_url,
    ).run(
        suite=arguments.suite,
        repeat=arguments.repeat,
        persona_set=arguments.persona_set,
        max_journey_cases=arguments.max_journey_cases,
        seed=arguments.seed,
        journey_ids=tuple(arguments.journey),
        include_scenarios=not arguments.skip_scenarios,
    )
    passed = sum(item.passed for item in attempts)
    print(f"CAMPAIGN RESULT: {passed}/{len(attempts)} attempts passed")
    print(f"CAMPAIGN REPORT: {report / 'summary.md'}")
    print(f"CAMPAIGN FAILURES: {report / 'failures.md'}")
    return 0 if passed == len(attempts) else 1


def _prepare(model: str, base_url: str, *, pull: bool) -> int:
    provider = OllamaModelProvider(model=model, base_url=base_url)
    health = provider.health()
    if health.available:
        print(f"PASS: Ollama {health.version}; model {model} is ready")
        return 0
    print(f"NOT READY: {health.reason}")
    if not pull:
        print("Run `python run.py prepare --pull` to explicitly download the model.")
        return 2
    executable = shutil.which("ollama")
    if executable is None:
        print("ERROR: ollama command is not installed or is absent from PATH")
        return 2
    completed = subprocess.run([executable, "pull", model], check=False)
    if completed.returncode != 0:
        return completed.returncode
    health = provider.health()
    print("PASS: model is ready" if health.available else f"ERROR: {health.reason}")
    return 0 if health.available else 2


def _run(arguments) -> int:
    if not 1 <= arguments.repeat <= 20:
        raise SystemExit("--repeat must be between 1 and 20")
    if not 0 < arguments.min_pass_rate <= 1:
        raise SystemExit("--min-pass-rate must be greater than 0 and at most 1")
    scenarios = discover_scenarios(LAB_ROOT / "scenarios", suite=arguments.suite)
    if arguments.scenario:
        scenarios = tuple(
            item for item in scenarios if item.scenario_id == arguments.scenario
        )
    if not scenarios:
        raise SystemExit("no scenarios matched")
    health = OllamaModelProvider(
        model=arguments.model, base_url=arguments.base_url
    ).health()
    if not health.available:
        print(f"NOT READY: {health.reason}")
        print("Run `python run.py prepare --pull` before live scenarios.")
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    runner = ScenarioRunner(
        project_root=PROJECT_ROOT,
        lab_root=LAB_ROOT,
        fixture_root=LAB_ROOT / "fixtures",
        model=arguments.model,
        base_url=arguments.base_url,
    )
    outcomes = []
    for repetition in range(1, arguments.repeat + 1):
        for scenario in scenarios:
            key = f"{stamp}-r{repetition}"
            print(
                f"RUN  {scenario.scenario_id} (repeat {repetition})",
                flush=True,
            )
            outcome = runner.run(scenario, run_id=key)
            outcomes.append(outcome)
            print(
                f"{'PASS' if outcome.passed else 'FAIL'} {scenario.scenario_id}",
                flush=True,
            )
    report = write_report(
        LAB_ROOT / "reports",
        tuple(outcomes),
        run_id=stamp,
        model=arguments.model,
        min_pass_rate=arguments.min_pass_rate,
    )
    passed = sum(item.passed for item in outcomes)
    stability = assess_stability(tuple(outcomes), min_pass_rate=arguments.min_pass_rate)
    stable = all(item["stable"] for item in stability)
    print(f"RESULT: {passed}/{len(outcomes)} scenarios passed")
    print(f"STABILITY: {'PASS' if stable else 'FAIL'}")
    print(f"REPORT: {report / 'summary.md'}")
    return 0 if stable else 1


def _report(*, path_only: bool) -> int:
    reports = LAB_ROOT / "reports"
    latest = reports / "latest.txt"
    if not latest.is_file():
        print("No reports yet.")
        return 2
    report = reports / latest.read_text(encoding="utf-8").strip() / "summary.md"
    print(report if path_only else report.read_text(encoding="utf-8"))
    return 0
