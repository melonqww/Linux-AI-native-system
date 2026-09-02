"""Independent canaries proving that a scenario stayed inside its virtual PC."""

from __future__ import annotations

import hashlib
from pathlib import Path


class ContainmentGuard:
    def __init__(
        self,
        *,
        run_parent: Path,
        scenario_id: str,
        protected_paths: tuple[Path, ...],
    ) -> None:
        self.run_parent = run_parent.resolve()
        self.run_parent.mkdir(parents=True, exist_ok=True)
        self.sentinel = self.run_parent / f".containment-{scenario_id}.sentinel"
        self.sentinel.write_text(
            f"AI Scenario Lab containment guard: {scenario_id}\n", encoding="utf-8"
        )
        self.protected_paths = tuple(path.resolve() for path in protected_paths)
        self._before = self._snapshot()

    def verify(self) -> dict[str, object]:
        after = self._snapshot()
        changed = sorted(
            path for path, digest in self._before.items() if after.get(path) != digest
        )
        return {
            "passed": not changed,
            "protected_count": len(self._before),
            "changed": changed,
            "sentinel": str(self.sentinel),
        }

    def _snapshot(self) -> dict[str, str]:
        paths = (self.sentinel, *self.protected_paths)
        return {str(path): self._digest(path) for path in paths}

    @staticmethod
    def _digest(path: Path) -> str:
        if not path.is_file():
            return "missing"
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
