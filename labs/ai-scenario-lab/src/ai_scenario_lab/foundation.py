"""Durable, process-isolated foundation campaigns. Never downloads a model."""

from __future__ import annotations

import hashlib
import json
import os
import random
import signal
import subprocess
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .adaptive_journeys import load_journey
from .campaign import _journey_cases
from .foundation_cases import foundation_scenarios
from .scenario import load_scenario


MODEL = "qwen3.5:2b"
BASE_URL = "http://127.0.0.1:11434"
GAPS = [
    "GNOME UI, systemd, Unix credentials, real mounts/ACL and inotify need Linux validation.",
    "Power loss/runtime restart with pending approval is not an end-to-end live lab case.",
    "Disk removal during an operation is not an end-to-end live lab case.",
    "Image case checks unsupported-input handling, not real image understanding.",
    "Text checks are structural/keyword checks, not a complete semantic quality review.",
    "Memory-35 tests recent recall after 35 turns, not unlimited retention beyond 8k context.",
    "Only search/copy and the listed module paths are live-tested; not every plugin.",
]


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def source_fingerprint(project: Path) -> str:
    listing = (
        subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=project,
            capture_output=True,
            check=True,
            timeout=15,
        )
        .stdout.decode("utf-8")
        .split("\0")
    )
    digest = hashlib.sha256()
    for name in sorted(set(listing)):
        if not name or Path(name).suffix not in {
            ".py",
            ".json",
            ".toml",
            ".ini",
            ".js",
            ".sh",
        }:
            continue
        path = project / name
        digest.update(name.encode("utf-8"))
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")
    return digest.hexdigest()


def prepare(lab: Path, project: Path, *, seeds=(7, 19), budget_seconds=14400) -> Path:
    if len(seeds) < 2 or len(seeds) > 5 or len(set(seeds)) != len(seeds):
        raise ValueError("foundation requires 2..5 distinct seeds")
    if any(type(seed) is not int or not 0 <= seed <= 1_000_000 for seed in seeds):
        raise ValueError("invalid seed")
    if not 60 <= budget_seconds <= 86400:
        raise ValueError("budget must be 60..86400 seconds")
    run = (
        lab
        / "reports"
        / "foundation"
        / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    )
    run.mkdir(parents=True, exist_ok=False)
    for folder in ("scenarios", "journeys", "fixtures"):
        for source in sorted((lab / folder).glob("*.json")):
            atomic_json(run / "inputs" / folder / source.name, read_json(source))
    for payload in foundation_scenarios():
        atomic_json(run / "inputs" / "scenarios" / f"{payload['id']}.json", payload)
    cases = []
    for identifier in ("contracts", "lab-tests"):
        cases.append({"id": identifier, "kind": "tests", "timeout_seconds": 600})
    cases.append({"id": "ollama-preflight", "kind": "preflight", "timeout_seconds": 30})
    journeys = tuple(
        load_journey(path) for path in sorted((run / "inputs/journeys").glob("*.json"))
    )
    journey_paths = {
        load_journey(path).journey_id: path.name
        for path in (run / "inputs/journeys").glob("*.json")
    }
    for seed in seeds:
        batch = []
        for path in sorted((run / "inputs/scenarios").glob("*.json")):
            scenario = load_scenario(path)
            batch.append(
                {
                    "id": f"s{seed}--{scenario.scenario_id}",
                    "subject": scenario.scenario_id,
                    "kind": "scenario",
                    "input": f"scenarios/{path.name}",
                    "seed": seed,
                    "language": scenario.locale,
                    "behavior": next(
                        (
                            tag
                            for tag in ("negative", "prompt-injection")
                            if tag in scenario.tags
                        ),
                        "standard",
                    ),
                    "mode": "mixed"
                    if "mixed" in scenario.tags
                    else "chat"
                    if "chat" in scenario.tags
                    else "action",
                    "capability": ",".join(
                        sorted(
                            {
                                cap
                                for turn in scenario.turns
                                for cap in turn.expect.get("capabilities", [])
                            }
                        )
                    )
                    or "none",
                    "decision": ",".join(
                        sorted(
                            {
                                turn.approval.value
                                for turn in scenario.turns
                                if turn.approval.value != "none"
                            }
                        )
                    )
                    or "none",
                    "input_modality": "text",
                    "memory_depth": len(scenario.turns),
                    "tags": list(scenario.tags),
                    "timeout_seconds": min(7200, len(scenario.turns) * 180 + 60),
                }
            )
        for case in _journey_cases(
            journeys, persona_set="all", max_cases=10000, seed=seed
        ):
            batch.append(
                {
                    "id": f"s{seed}--{case.case_id}",
                    "subject": case.case_id,
                    "kind": "journey",
                    "input": f"journeys/{journey_paths[case.journey.journey_id]}",
                    "seed": seed,
                    **case.dimensions,
                    "input_modality": case.dimensions["input_kind"],
                    "behavior": ",".join(case.dimensions["behaviors"]) or "standard",
                    "timeout_seconds": case.journey.max_turns * 180 + 60,
                }
            )
        random.Random(seed).shuffle(batch)
        cases.extend(batch)
    manifest = {
        "schema_version": 1,
        "profile": "foundation-v1",
        "run_id": run.name,
        "model": MODEL,
        "base_url": BASE_URL,
        "context_tokens": 8192,
        "python": sys.version,
        "platform": sys.platform,
        "seeds": list(seeds),
        "budget_seconds": budget_seconds,
        "max_turn_ms": 90000,
        "source_fingerprint": source_fingerprint(project),
        "cases": cases,
        "inputs": {
            str(path.relative_to(run)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((run / "inputs").rglob("*.json"))
        },
        "known_gaps": GAPS,
        "automatic_retries": 0,
    }
    atomic_json(run / "manifest.json", manifest)
    publish(run, manifest, "prepared")
    atomic_json(
        lab / "reports/latest.json",
        {"run": str(run.resolve()), "summary": str((run / "summary.md").resolve())},
    )
    return run


def verify_inputs(run: Path, manifest: dict) -> None:
    for relative, expected in manifest["inputs"].items():
        path = (run / relative).resolve()
        if not path.is_relative_to((run / "inputs").resolve()):
            raise ValueError("input outside snapshot")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"snapshot changed: {relative}")


def resolve_run(lab: Path, name: str | None) -> Path:
    run = Path(name) if name else Path(read_json(lab / "reports/latest.json")["run"])
    run = run.resolve()
    if (
        not run.is_relative_to((lab / "reports/foundation").resolve())
        or not (run / "manifest.json").is_file()
    ):
        raise ValueError("not a foundation run directory")
    return run


@contextmanager
def campaign_lock(lab: Path):
    """OS-owned lock: survives file existence, releases on process death."""
    path = lab / "reports/.foundation.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def run_process(
    command: list[str], *, cwd: Path, log: Path, timeout: float, heartbeat=None
) -> dict:
    started = time.monotonic()
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    with log.open("wb") as stream:
        group = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            env=environment,
            **group,
        )
        try:
            while True:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    _stop_process(process)
                    return {
                        "status": "timeout",
                        "duration_ms": (time.monotonic() - started) * 1000,
                    }
                try:
                    code = process.wait(timeout=min(5, remaining))
                    return {
                        "status": "passed" if code == 0 else "error",
                        "exit_code": code,
                        "duration_ms": (time.monotonic() - started) * 1000,
                    }
                except subprocess.TimeoutExpired:
                    if heartbeat:
                        heartbeat()
        finally:
            if process.poll() is None:
                _stop_process(process)


def _stop_process(process) -> None:
    # Only the worker tree created by this supervisor, never the user's Ollama.
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


def supervise(lab: Path, project: Path, run: Path) -> int:
    manifest = read_json(run / "manifest.json")
    with campaign_lock(lab):
        if read_json(run / "progress.json")["state"] != "prepared":
            raise ValueError(
                "run already started; prepare a new run (no silent resume)"
            )
        started = time.monotonic()
        state, reason = "running", None
        try:
            verify_inputs(run, manifest)
            if source_fingerprint(project) != manifest["source_fingerprint"]:
                raise ValueError("source changed since prepare; prepare a new manifest")
            publish(run, manifest, state)
            for case in manifest["cases"]:
                remaining = manifest["budget_seconds"] - (time.monotonic() - started)
                if remaining <= 0:
                    state, reason = "incomplete", "campaign_deadline"
                    break
                if source_fingerprint(project) != manifest["source_fingerprint"]:
                    state, reason = "incomplete", "source_changed"
                    break
                identifier = case["id"]
                publish(run, manifest, state, current=identifier)
                command = [
                    sys.executable,
                    str(lab / "run.py"),
                    "foundation-worker",
                    "--run",
                    str(run),
                    "--case",
                    identifier,
                ]
                process = run_process(
                    command,
                    cwd=project,
                    log=run / f"{identifier}.log",
                    timeout=min(remaining, case["timeout_seconds"]),
                    heartbeat=lambda: publish(run, manifest, state, current=identifier),
                )
                result_path = run / "results" / f"{identifier}.json"
                if process["status"] != "passed" or not result_path.is_file():
                    atomic_json(
                        result_path,
                        {
                            "id": identifier,
                            **process,
                            "status": "timeout"
                            if process["status"] == "timeout"
                            else "error",
                            "layer": "LAB",
                            "reason": "worker_timeout_or_crash",
                            "log": f"{identifier}.log",
                        },
                    )
                result = read_result(run, case)
                publish(run, manifest, state)
                if (result.get("diagnostic") or {}).get("layer") == "CONTAINMENT":
                    state, reason = "blocked", "containment_breach"
                    break
                if (
                    case["kind"] in {"tests", "preflight"}
                    and result["status"] != "passed"
                ):
                    state, reason = "blocked", f"{identifier}_failed"
                    break
            else:
                state = "completed"
            if source_fingerprint(project) != manifest["source_fingerprint"]:
                state, reason = "incomplete", "source_changed"
        except Exception as error:
            state, reason = "interrupted", f"{type(error).__name__}: {error}"
        finally:
            publish(run, manifest, state, reason=reason)
    return 0 if read_json(run / "summary.json")["verdict"] == "backend_candidate" else 1


def publish(
    run: Path, manifest: dict, state: str, *, current=None, reason=None
) -> dict:
    results = [read_result(run, case) for case in manifest["cases"]]
    counts = dict(Counter(result["status"] for result in results))
    slow = [result["id"] for result in results if result.get("slow_turns")]
    failures = [
        result for result in results if result["status"] not in {"passed", "not_run"}
    ]
    verdict = (
        "incomplete"
        if state != "completed" or counts.get("not_run", 0)
        else "problems_found"
        if failures
        else "performance_problems"
        if slow
        else "backend_candidate"
    )
    dimensions = {}
    for axis in (
        "language",
        "behavior",
        "memory_depth",
        "kind",
        "mode",
        "capability",
        "decision",
        "failure_kind",
        "input_modality",
    ):
        cells = {}
        for case, result in zip(manifest["cases"], results, strict=True):
            key = str(result.get("context", {}).get(axis, case.get(axis, "unobserved")))
            cell = cells.setdefault(key, Counter())
            cell[result["status"]] += 1
        dimensions[axis] = {key: dict(value) for key, value in cells.items()}
    groups, unknown, repeats = {}, [], {}
    for result in failures:
        diagnostic = result.get("diagnostic") or {}
        if diagnostic.get("layer") in {None, "UNKNOWN"} or not diagnostic.get(
            "fingerprint"
        ):
            unknown.append(result["id"])
        else:
            groups.setdefault(diagnostic["fingerprint"], []).append(result["id"])
    for case, result in zip(manifest["cases"], results, strict=True):
        repeats.setdefault(case.get("subject", case["id"]), []).append(result["status"])
    summary = {
        "run_id": manifest["run_id"],
        "state": state,
        "verdict": verdict,
        "reason": reason,
        "counts": counts,
        "planned": len(results),
        "slow_cases": slow,
        "coverage": dimensions,
        "repeat_results": repeats,
        "results": results,
        "known_gaps": manifest["known_gaps"],
        "beta_0_1_released": False,
    }
    atomic_json(run / "summary.json", summary)
    atomic_json(
        run / "failures.json",
        {
            "failures": failures,
            "groups": groups,
            "unknown": unknown,
            "slow_cases": slow,
        },
    )
    lines = [
        "# Foundation report",
        "",
        f"State: {state}; verdict: **{verdict}**.",
        "",
        f"Planned: {len(results)}. Counts: {counts}.",
        f"Reason: {reason or 'none'}.",
        "",
        "Passing means a backend candidate in the tested scope, NOT a Linux/beta release.",
        "No failed repeat is hidden by another passing repeat. No automatic retries.",
        "",
        "## Results",
        "",
        "| Case | Status | Detail |",
        "|---|---|---|",
    ]
    for case, result in zip(manifest["cases"], results, strict=True):
        link = (
            f"[trace](traces/{case['id']}.json)"
            if result.get("trace")
            else f"[log]({case['id']}.log)"
        )
        if result["status"] == "not_run":
            link = "not executed"
        lines.append(f"| {case['id']} | {result['status']} | {link} |")
    lines += [
        "",
        "## Coverage (executed results, not universal coverage)",
        "",
        "| Axis | Value | Status counts |",
        "|---|---|---|",
        *[
            f"| {axis} | {key} | {value} |"
            for axis, cells in dimensions.items()
            for key, value in cells.items()
        ],
        "",
        "## Performance",
        "",
        f"Turn budget: {manifest['max_turn_ms']} ms. Slow cases: {slow or 'none observed'}.",
        "",
        "## Known gaps",
        "",
        *[f"- {gap}" for gap in manifest["known_gaps"]],
    ]
    temporary = run / ".summary.md.tmp"
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, run / "summary.md")
    atomic_json(
        run / "progress.json",
        {
            "state": state,
            "current": current,
            "reason": reason,
            "heartbeat_unix": time.time(),
            "pid": os.getpid(),
            "counts": counts,
        },
    )
    return summary


def read_result(run: Path, case: dict) -> dict:
    path = run / "results" / f"{case['id']}.json"
    if not path.is_file():
        return {"id": case["id"], "status": "not_run"}
    try:
        result = read_json(path)
        if (
            not isinstance(result, dict)
            or result.get("id") != case["id"]
            or result.get("status") not in {"passed", "failed", "timeout", "error"}
        ):
            raise ValueError("wrong case id or status")
        return result
    except (ValueError, OSError) as error:
        return {
            "id": case["id"],
            "status": "error",
            "layer": "LAB",
            "reason": f"invalid_result: {error}",
        }


def status(run: Path) -> dict:
    progress = read_json(run / "progress.json")
    if (
        progress["state"] == "running"
        and time.time() - progress["heartbeat_unix"] > 120
    ):
        progress = {
            **progress,
            "state": "interrupted_or_unresponsive",
            "warning": "heartbeat is stale; results are partial, not a pass",
        }
    launch = run / "launch.json"
    if progress["state"] == "prepared" and launch.is_file():
        dispatched = read_json(launch)
        progress = {
            **progress,
            "state": "startup_unconfirmed"
            if time.time() - dispatched["launched_unix"] > 30
            else "starting",
            "supervisor_log": str(run / "supervisor.log"),
        }
    return {**progress, "report": str(run / "summary.md")}


def start_background(lab: Path, run: Path) -> int:
    if read_json(run / "progress.json")["state"] != "prepared":
        raise ValueError("run already started")
    command = [
        sys.executable,
        str(lab / "run.py"),
        "foundation",
        "run",
        "--run",
        str(run),
    ]
    options = (
        {
            "creationflags": subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
        }
        if os.name == "nt"
        else {"start_new_session": True}
    )
    with (run / "supervisor.log").open("ab") as stream:
        process = subprocess.Popen(
            command,
            cwd=lab,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            **options,
        )
    atomic_json(run / "launch.json", {"pid": process.pid, "launched_unix": time.time()})
    return process.pid
