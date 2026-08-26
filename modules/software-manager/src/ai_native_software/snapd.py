from __future__ import annotations

import http.client
import json
import re
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .contracts import BackendProgress


_PACKAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$")
_CHANGE_ID = re.compile(r"^[0-9]{1,20}$")
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class SnapdError(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: Path, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        connection.connect(str(self.socket_path))
        self.sock = connection


class SnapdClient:
    def __init__(
        self,
        socket_path: Path = Path("/run/snapd.socket"),
        *,
        timeout: float = 15.0,
        connection_factory: Callable[[], http.client.HTTPConnection] | None = None,
    ) -> None:
        if timeout <= 0 or timeout > 60:
            raise ValueError("timeout must be between 0 and 60 seconds")
        self.socket_path = Path(socket_path)
        self.timeout = timeout
        self._connection_factory = connection_factory

    def start(self, action: str, package_name: str, *, channel: str = "stable") -> str:
        if action not in {"install", "remove"}:
            raise ValueError("unsupported_snap_action")
        self._validate_package(package_name)
        body: dict[str, object] = {"action": action}
        if action == "install":
            if channel != "stable":
                raise ValueError("only_stable_channel_supported")
            body["channel"] = channel
        payload = self._request(
            "POST",
            f"/v2/snaps/{quote(package_name, safe='')}",
            body,
            allow_interaction=True,
        )
        change_id = payload.get("change")
        if not isinstance(change_id, str) or _CHANGE_ID.fullmatch(change_id) is None:
            raise SnapdError("invalid_change_id")
        return change_id

    def progress(self, change_id: str) -> BackendProgress:
        self._validate_change(change_id)
        payload = self._request("GET", f"/v2/changes/{change_id}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise SnapdError("invalid_change_response")
        return self._parse_progress(result)

    def abort(self, change_id: str) -> None:
        self._validate_change(change_id)
        self._request(
            "POST",
            f"/v2/changes/{change_id}",
            {"action": "abort"},
            allow_interaction=True,
        )

    @staticmethod
    def _parse_progress(change: dict[str, Any]) -> BackendProgress:
        status = str(change.get("status", "")).casefold()
        ready = change.get("ready") is True
        tasks = change.get("tasks")
        task_items = tasks if isinstance(tasks, list) else []

        totals = 0
        completed = 0
        measurable = False
        active_done: int | None = None
        active_total: int | None = None
        active_kind = ""
        for item in task_items[:256]:
            if not isinstance(item, dict):
                continue
            task_status = str(item.get("status", "")).casefold()
            if task_status == "doing" or (task_status == "wait" and not active_kind):
                active_kind = str(item.get("kind", "")).casefold()
            progress = item.get("progress")
            if not isinstance(progress, dict):
                continue
            done = progress.get("done")
            total = progress.get("total")
            if (
                isinstance(done, int)
                and not isinstance(done, bool)
                and isinstance(total, int)
                and not isinstance(total, bool)
                and 0 <= done <= total
                and total > 0
            ):
                measurable = True
                completed += done
                totals += total
                if task_status == "doing":
                    active_done = done
                    active_total = total

        percent = None
        if active_done is not None and active_total is not None:
            percent = max(0, min(100, round(active_done * 100 / active_total)))
        elif measurable and totals > 0:
            percent = max(0, min(100, round(completed * 100 / totals)))

        if ready and status == "done":
            return BackendProgress("completed", "completed", 100)
        if status == "error":
            return BackendProgress("failed", "failed", percent, "snap_change_failed")
        if status in {"abort", "hold", "undone"}:
            return BackendProgress("interrupted", "interrupted", percent)

        phase = "working"
        if any(word in active_kind for word in ("download", "fetch")):
            phase = "downloading"
        elif any(word in active_kind for word in ("remove", "unlink", "discard")):
            phase = "removing"
        elif any(word in active_kind for word in ("install", "setup", "mount", "connect")):
            phase = "installing"
        return BackendProgress("running", phase, percent)

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
        *,
        allow_interaction: bool = False,
    ) -> dict[str, Any]:
        connection = (
            self._connection_factory()
            if self._connection_factory is not None
            else _UnixHTTPConnection(self.socket_path, self.timeout)
        )
        encoded = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        headers = {"Accept": "application/json"}
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        if allow_interaction:
            headers["X-Allow-Interaction"] = "true"
        try:
            connection.request(method, path, body=encoded, headers=headers)
            response = connection.getresponse()
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except (OSError, http.client.HTTPException) as error:
            raise SnapdError("snapd_unavailable") from error
        finally:
            connection.close()
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise SnapdError("snapd_response_too_large")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SnapdError("invalid_snapd_json") from error
        if not isinstance(payload, dict):
            raise SnapdError("invalid_snapd_response")
        if response.status >= 400 or payload.get("type") == "error":
            result = payload.get("result")
            kind = result.get("kind") if isinstance(result, dict) else None
            message = result.get("message") if isinstance(result, dict) else None
            code = str(kind) if isinstance(kind, str) and kind else "snapd_request_failed"
            raise SnapdError(code, str(message) if isinstance(message, str) else None)
        return payload

    @staticmethod
    def _validate_package(package_name: str) -> None:
        if not isinstance(package_name, str) or _PACKAGE_NAME.fullmatch(package_name) is None:
            raise ValueError("invalid_snap_package_name")

    @staticmethod
    def _validate_change(change_id: str) -> None:
        if not isinstance(change_id, str) or _CHANGE_ID.fullmatch(change_id) is None:
            raise ValueError("invalid_snap_change_id")
