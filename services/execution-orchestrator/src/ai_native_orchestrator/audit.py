"""Thread-safe metadata-only JSONL audit for orchestration events."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock


class OrchestrationAuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()

    def append(self, event: dict[str, object]) -> None:
        safe = dict(event)
        safe["timestamp"] = datetime.now(UTC).isoformat()
        safe["schema"] = "orchestration.audit.v1"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(safe, ensure_ascii=False, sort_keys=True))
                stream.write("\n")
