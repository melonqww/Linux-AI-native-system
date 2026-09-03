"""One bounded worker per case; all system effects use existing lab adapters."""

from __future__ import annotations

import subprocess
import os
import sys
import time
import urllib.request
import json
from pathlib import Path
from xml.etree import ElementTree

from .adaptive_journeys import JourneyRunner, load_journey
from .campaign import (
    _journey_cases,
    _journey_context,
    _journey_evidence,
    _json_value,
    _scenario_context,
    _scenario_evidence,
)
from .diagnostics import classify_problem
from .foundation import atomic_json, read_json, verify_inputs
from .runner import ScenarioRunner
from .scenario import load_scenario


def model_identity(manifest: dict) -> dict:
    # A missing server/tag is a prerequisite failure, never an implicit pull.
    with urllib.request.urlopen(
        manifest["base_url"] + "/api/tags", timeout=10
    ) as response:
        tags = json.load(response)
    match = next(
        (item for item in tags["models"] if item.get("name") == manifest["model"]), None
    )
    if match is None or not match.get("digest"):
        raise ValueError("exact model tag/digest is not available")
    with urllib.request.urlopen(
        manifest["base_url"] + "/api/version", timeout=10
    ) as response:
        version = json.load(response)
    return {
        "model": manifest["model"],
        "digest": match["digest"],
        "ollama_version": version["version"],
    }


def run_case(lab: Path, project: Path, run: Path, identifier: str) -> int:
    manifest = read_json(run / "manifest.json")
    case = next(item for item in manifest["cases"] if item["id"] == identifier)
    started = time.monotonic()
    result = {"id": identifier, "status": "error"}
    try:
        verify_inputs(run, manifest)
        if case["kind"] == "tests":
            targets = (
                ["services", "modules", "apps", "deployments", "tests"]
                if identifier == "contracts"
                else [str(lab / "tests")]
            )
            junit = run / f"{identifier}.xml"
            test_environment = {**os.environ, "AI_NATIVE_RUN_OLLAMA_EVALS": "0"}
            # Prerequisites are deterministic; live-model checks run later.
            # User PYTEST_ADDOPTS must not silently narrow the required suite.
            test_environment.pop("PYTEST_ADDOPTS", None)
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", *targets, "-q", f"--junitxml={junit}"],
                cwd=project,
                check=False,
                timeout=case["timeout_seconds"] - 15,
                env=test_environment,
            )
            tests = list(ElementTree.parse(junit).getroot().iter("testcase"))
            skips = [
                item.attrib.get("name")
                for item in tests
                if item.find("skipped") is not None
            ]
            problems = [
                item.attrib.get("name")
                for item in tests
                if item.find("failure") is not None or item.find("error") is not None
            ]
            passed = (
                completed.returncode == 0
                and bool(tests)
                and len(skips) < len(tests)
                and not problems
            )
            result.update(
                status="passed" if passed else "failed",
                tests=len(tests),
                skipped=skips,
                failed_tests=problems,
                junit=junit.name,
                exit_code=completed.returncode,
            )
        elif case["kind"] == "preflight":
            identity = model_identity(manifest)
            atomic_json(run / "model.json", identity)
            result.update(status="passed", **identity)
        else:
            if model_identity(manifest) != read_json(run / "model.json"):
                raise ValueError(
                    "model digest or Ollama version changed during campaign"
                )
            source = run / "inputs" / case["input"]
            options = dict(
                project_root=project,
                lab_root=lab,
                fixture_root=run / "inputs/fixtures",
                model=manifest["model"],
                base_url=manifest["base_url"],
                on_turn=lambda payload: atomic_json(
                    run
                    / "partial"
                    / identifier
                    / f"turn-{payload['turn'].ordinal}.json",
                    _json_value(payload),
                ),
            )
            run_id = f"{run.name}-{identifier}"
            if case["kind"] == "scenario":
                spec = load_scenario(source)
                outcome = ScenarioRunner(**options).run(spec, run_id=run_id)
                context = _scenario_context(spec, outcome)
                evidence = _scenario_evidence(outcome)
                complete = len(outcome.turns) == len(spec.turns) and all(
                    turn.checks for turn in outcome.turns
                )
                mutations = None
            else:
                variants = _journey_cases(
                    (load_journey(source),),
                    persona_set="all",
                    max_cases=10000,
                    seed=case["seed"],
                )
                variant = next(
                    item for item in variants if item.case_id == case["subject"]
                )
                outcome = JourneyRunner(**options).run(variant.journey, run_id=run_id)
                context = _journey_context(variant, outcome)
                evidence = _journey_evidence(outcome)
                complete = bool(outcome.turns) and outcome.evaluation is not None
                mutations = _json_value(variant.mutations)
            trace = f"traces/{identifier}.json"
            atomic_json(
                run / trace,
                {"case": case, "outcome": _json_value(outcome), "mutations": mutations},
            )
            passed = outcome.passed and complete and outcome.error is None
            if not complete:
                evidence = {"code": "incomplete_case"}
            diagnostic = (
                None if passed else _json_value(classify_problem(context, evidence))
            )
            result.update(
                status="passed" if passed else "failed",
                trace=trace,
                diagnostic=diagnostic,
                context=_json_value(context),
                slow_turns=[
                    turn.ordinal
                    for turn in outcome.turns
                    if turn.duration_ms > manifest["max_turn_ms"]
                ],
            )
    except Exception as error:
        result.update(
            status="error", layer="LAB", reason=f"{type(error).__name__}: {error}"
        )
        print(result["reason"], flush=True)
    result["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
    atomic_json(run / "results" / f"{identifier}.json", result)
    # A failed assertion is valid evidence, not a crashed worker.
    return 0
