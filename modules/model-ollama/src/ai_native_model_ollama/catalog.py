"""Persistent user decisions and a multi-model Ollama catalog."""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from .manager import OllamaModelManager


_DECISIONS = frozenset({"download", "later", "never"})


@dataclass(frozen=True)
class ModelDefinition:
    model_id: str
    provider_model: str
    display_name: str
    role: str
    required: bool
    estimated_size_bytes: int | None = None


class ModelDecisionStore:
    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self._lock = threading.RLock()
        if self.database.exists() and self.database.is_symlink():
            raise ValueError("model decision database cannot be a symlink")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS model_decisions (
                    model_id TEXT PRIMARY KEY,
                    decision TEXT NOT NULL,
                    dismissed_until TEXT,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute("PRAGMA user_version = 1")
        if os.name == "posix":
            os.chmod(self.database, 0o600)

    def get(self, model_id: str) -> tuple[str | None, datetime | None]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT decision, dismissed_until FROM model_decisions WHERE model_id = ?",
                (model_id,),
            ).fetchone()
        if row is None:
            return None, None
        dismissed = datetime.fromisoformat(row[1]).astimezone(UTC) if row[1] else None
        return str(row[0]), dismissed

    def set(self, model_id: str, decision: str, *, now: datetime) -> None:
        if decision not in _DECISIONS:
            raise ValueError("unsupported model decision")
        dismissed = now + timedelta(hours=24) if decision == "later" else None
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO model_decisions(model_id, decision, dismissed_until, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(model_id) DO UPDATE SET decision = excluded.decision,
                   dismissed_until = excluded.dismissed_until,
                   updated_at = excluded.updated_at""",
                (
                    model_id,
                    decision,
                    dismissed.isoformat() if dismissed else None,
                    now.isoformat(),
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection


class OllamaModelCatalog:
    def __init__(
        self,
        definitions: tuple[ModelDefinition, ...],
        decisions: ModelDecisionStore,
        *,
        base_url: str,
        now_fn: Callable[[], datetime] | None = None,
        manager_factory: Callable[..., OllamaModelManager] = OllamaModelManager,
    ) -> None:
        if not definitions or len({item.model_id for item in definitions}) != len(definitions):
            raise ValueError("model definitions must be non-empty and unique")
        if sum(1 for item in definitions if item.required) != 1:
            raise ValueError("exactly one base model is required")
        self.definitions = {item.model_id: item for item in definitions}
        self.decisions = decisions
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._lifecycle_lock = threading.RLock()
        self.managers = {
            item.model_id: manager_factory(
                model=item.provider_model,
                base_url=base_url,
                lifecycle_lock=self._lifecycle_lock,
            )
            for item in definitions
        }

    def catalog(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "models": [self._view(item) for item in self.definitions.values()],
        }

    def ensure_base(self) -> dict[str, object]:
        definition = next(item for item in self.definitions.values() if item.required)
        return self._ensure(definition).to_dict()

    def respond(self, model_id: str, decision: str) -> dict[str, object]:
        definition = self._definition(model_id)
        if decision not in _DECISIONS:
            raise ValueError("decision must be download, later or never")
        self.decisions.set(model_id, decision, now=self._now())
        return self._view(definition)

    def stop(self) -> None:
        for manager in self.managers.values():
            manager.stop()

    def _ensure(self, definition: ModelDefinition):
        manager = self.managers[definition.model_id]
        if manager.installed():
            return manager.ensure()
        decision, dismissed = self.decisions.get(definition.model_id)
        if decision == "download":
            return manager.ensure()
        now = self._now()
        if decision == "later" and dismissed is not None and dismissed > now:
            return manager.policy_status("deferred", reason="user_deferred")
        if decision == "never":
            return manager.policy_status("declined", reason="user_declined")
        return manager.policy_status("consent_required", reason="user_decision_required")

    def _view(self, definition: ModelDefinition) -> dict[str, object]:
        manager = self.managers[definition.model_id]
        installed = manager.installed()
        decision, dismissed = self.decisions.get(definition.model_id)
        now = self._now()
        prompt_required = (
            not installed
            and decision != "never"
            and not (decision == "later" and dismissed is not None and dismissed > now)
            and decision != "download"
        )
        if installed:
            status = manager.policy_status("ready")
        elif decision == "download":
            status = manager.ensure()
        elif decision == "never":
            status = manager.policy_status("declined", reason="user_declined")
        elif decision == "later" and dismissed is not None and dismissed > now:
            status = manager.policy_status("deferred", reason="user_deferred")
        else:
            status = manager.policy_status("consent_required", reason="user_decision_required")
        return {
            "model_id": definition.model_id,
            "provider": "ollama",
            "provider_model": definition.provider_model,
            "display_name": definition.display_name,
            "role": definition.role,
            "required": definition.required,
            "estimated_size_bytes": definition.estimated_size_bytes,
            "installed": installed,
            "decision": decision or "unset",
            "prompt_required": prompt_required,
            "state": status.state,
            "progress_percent": status.progress_percent,
            "completed_bytes": status.completed_bytes,
            "total_bytes": status.total_bytes,
            "reason": status.reason,
        }

    def _definition(self, model_id: str) -> ModelDefinition:
        if not isinstance(model_id, str) or model_id not in self.definitions:
            raise ValueError("unknown model_id")
        return self.definitions[model_id]

    def _now(self) -> datetime:
        value = self._now_fn()
        if value.tzinfo is None:
            raise ValueError("model catalog clock must be timezone-aware")
        return value.astimezone(UTC)
