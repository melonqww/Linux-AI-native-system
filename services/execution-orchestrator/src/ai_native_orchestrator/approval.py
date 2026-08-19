"""Server-owned R1 approval sessions and safe destination role resolution."""

from __future__ import annotations

import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from time import monotonic

from ai_native_intents import PlanStep
from ai_native_storage import MaterializePlan

from .contracts import ApprovalRequest, StepExecution


class ApprovalSessionError(ValueError):
    def __init__(self, message: str, *, expired: tuple[ApprovalSession, ...] = ()) -> None:
        super().__init__(message)
        self.expired = expired


@dataclass(frozen=True)
class ApprovalSession:
    run_id: str
    request: ApprovalRequest
    materialize_plan: MaterializePlan
    step: PlanStep
    prior_steps: tuple[StepExecution, ...]
    active_collection_id: str | None


@dataclass(frozen=True)
class _StoredSession:
    session: ApprovalSession
    expires_at: float


class ApprovalSessionStore:
    def __init__(self, *, capacity: int = 100, ttl_seconds: float = 300) -> None:
        if capacity < 1 or ttl_seconds <= 0:
            raise ValueError("approval session limits must be positive")
        self.capacity = capacity
        self.ttl_seconds = ttl_seconds
        self._lock = RLock()
        self._sessions: OrderedDict[str, _StoredSession] = OrderedDict()

    def put(self, session: ApprovalSession) -> tuple[ApprovalSession, ...]:
        evicted: list[ApprovalSession] = []
        with self._lock:
            evicted.extend(self._purge())
            key = session.request.approval_request_id
            self._sessions[key] = _StoredSession(session, monotonic() + self.ttl_seconds)
            self._sessions.move_to_end(key)
            while len(self._sessions) > self.capacity:
                evicted.append(self._sessions.popitem(last=False)[1].session)
        return tuple(evicted)

    def claim(self, request_id: str) -> tuple[ApprovalSession, tuple[ApprovalSession, ...]]:
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
            raise ApprovalSessionError("invalid_approval_request_id")
        with self._lock:
            expired = tuple(self._purge())
            stored = self._sessions.pop(request_id, None)
        if stored is None:
            raise ApprovalSessionError(
                "approval_not_found_expired_or_consumed", expired=expired
            )
        return stored.session, expired

    def _purge(self) -> list[ApprovalSession]:
        now = monotonic()
        expired = [key for key, value in self._sessions.items() if value.expires_at <= now]
        return [self._sessions.pop(key).session for key in expired]


class DestinationResolver:
    _INVALID_CHILD = re.compile(r'[<>:"/\\|?*]')

    def __init__(self, *, home: Path | None = None, roles: dict[str, Path] | None = None) -> None:
        self.home = (home or Path.home()).resolve()
        self.roles = roles or {
            "desktop": self._xdg("DESKTOP", "Desktop"),
            "documents": self._xdg("DOCUMENTS", "Documents"),
            "downloads": self._xdg("DOWNLOAD", "Downloads"),
        }

    def resolve(
        self,
        destination: str,
        *,
        directory_name: str | None = None,
        trusted_last_destination: str | None = None,
    ) -> Path:
        if destination in self.roles:
            base = self.roles[destination]
        elif trusted_last_destination is not None and destination == trusted_last_destination:
            base = Path(destination)
        else:
            raise ValueError("destination is not a trusted role or prior destination")
        if directory_name is None:
            return base
        name = directory_name.strip()
        if (
            not name
            or len(name) > 128
            or name in {".", ".."}
            or name[-1] in {" ", "."}
            or self._INVALID_CHILD.search(name)
            or any(ord(character) < 32 for character in name)
        ):
            raise ValueError("directory_name is not a safe child name")
        return base / name

    def _xdg(self, key: str, fallback: str) -> Path:
        config = self.home / ".config" / "user-dirs.dirs"
        try:
            for line in config.read_text(encoding="utf-8").splitlines():
                prefix = f'XDG_{key}_DIR="'
                if line.startswith(prefix) and line.endswith('"'):
                    value = line[len(prefix):-1].replace("$HOME", str(self.home))
                    candidate = Path(os.path.expandvars(value))
                    if candidate.is_absolute():
                        return candidate
        except OSError:
            pass
        return self.home / fallback
