"""Atomically create or migrate the user runtime environment file."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


LEGACY_MODEL_LINE = "AI_NATIVE_INTENT_MODEL=qwen3:1.7b"
CURRENT_MODEL_LINE = "AI_NATIVE_INTENT_MODEL=qwen3.5:2b"
OLLAMA_URL_LINE = "AI_NATIVE_OLLAMA_URL=http://127.0.0.1:11434"


def migrate_runtime_env(environment_file: Path) -> bool:
    path = Path(environment_file).expanduser().absolute()
    if path.is_symlink():
        raise ValueError("runtime environment file cannot be a symlink")
    if path.exists() and not path.is_file():
        raise ValueError("runtime environment path must be a regular file")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    if path.exists():
        original = path.read_text(encoding="utf-8")
        lines = original.splitlines()
        migrated = [
            CURRENT_MODEL_LINE if line == LEGACY_MODEL_LINE else line
            for line in lines
        ]
        changed = migrated != lines
        content = "\n".join(migrated) + ("\n" if original.endswith("\n") else "")
    else:
        changed = True
        content = f"{CURRENT_MODEL_LINE}\n{OLLAMA_URL_LINE}\n"

    if changed:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".runtime.env-", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    os.chmod(path, 0o600)
    return changed


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: migrate_runtime_env.py PATH", file=sys.stderr)
        return 2
    try:
        migrate_runtime_env(Path(arguments[0]))
    except (OSError, UnicodeError, ValueError) as error:
        print(f"FAIL: runtime environment migration failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
