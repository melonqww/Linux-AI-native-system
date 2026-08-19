"""Tiny isolated health/control host for a validated Python module entrypoint."""

import importlib
import json
import re
import sys


_OPERATION = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload), flush=True)


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    module_name = sys.argv[1]
    try:
        module = importlib.import_module(module_name)
        start = getattr(module, "worker_start", None)
        if callable(start):
            start()
    except Exception as error:
        emit({"event": "failed", "error": f"{type(error).__name__}: {error}"})
        return 1
    emit({"event": "ready", "module": module_name})
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            emit({"event": "error", "error": "invalid_json"})
            continue
        command = request.get("command")
        if command == "health":
            payload: dict[str, object] = {"event": "healthy", "module": module_name}
            health = getattr(module, "worker_health", None)
            if callable(health):
                details = health()
                if isinstance(details, dict):
                    payload["details"] = details
            emit(payload)
        elif command == "shutdown":
            stop = getattr(module, "worker_stop", None)
            if callable(stop):
                stop()
            emit({"event": "stopped", "module": module_name})
            return 0
        elif command == "invoke":
            operation = request.get("operation")
            payload = request.get("payload")
            invoke = getattr(module, "worker_invoke", None)
            if (
                not isinstance(operation, str)
                or _OPERATION.fullmatch(operation) is None
                or not isinstance(payload, dict)
                or not callable(invoke)
            ):
                emit({"event": "error", "error": "invalid_invocation"})
                continue
            try:
                result = invoke(operation, payload)
            except (OSError, RuntimeError, ValueError):
                emit({"event": "error", "error": "invocation_failed"})
                continue
            if not isinstance(result, dict):
                emit({"event": "error", "error": "invalid_result"})
                continue
            try:
                json.dumps(result)
            except (TypeError, ValueError):
                emit({"event": "error", "error": "invalid_result"})
                continue
            emit({"event": "result", "module": module_name, "result": result})
        else:
            emit({"event": "error", "error": "unknown_command"})
    stop = getattr(module, "worker_stop", None)
    if callable(stop):
        stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
