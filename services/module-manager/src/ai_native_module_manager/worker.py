"""Tiny isolated health/control host for a validated Python module entrypoint."""

import importlib
import json
import sys


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload), flush=True)


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    module_name = sys.argv[1]
    try:
        importlib.import_module(module_name)
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
            emit({"event": "healthy", "module": module_name})
        elif command == "shutdown":
            emit({"event": "stopped", "module": module_name})
            return 0
        else:
            emit({"event": "error", "error": "unknown_command"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
