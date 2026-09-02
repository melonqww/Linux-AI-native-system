from __future__ import annotations

import argparse
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ai_native_intents import OllamaModelProvider

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
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.command == "prepare":
        return _prepare(arguments.model, arguments.base_url, pull=arguments.pull)
    if arguments.command == "run":
        return _run(arguments)
    return _report(path_only=arguments.path)


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
    stability = assess_stability(
        tuple(outcomes), min_pass_rate=arguments.min_pass_rate
    )
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
