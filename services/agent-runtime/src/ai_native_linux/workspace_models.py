"""Installed Ollama model selection for the workspace's shared intent provider."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from threading import RLock

from ai_native_intents import IntentProviderError, OllamaModelProvider
from ai_native_workspace import WorkspaceRuntime


class WorkspaceModelSelection:
    def __init__(
        self,
        provider: OllamaModelProvider,
        workspace: WorkspaceRuntime,
        state_path: Path,
        *,
        default_status: Callable[[], dict[str, object]] | None = None,
    ) -> None:
        self.provider = provider
        self.workspace = workspace
        self.default_model = provider.model
        self.default_status = default_status
        self.state_path = Path(state_path)
        self._lock = RLock()
        self._capabilities: dict[tuple[str, str], bool] = {}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.state_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS workspace_model (id INTEGER PRIMARY KEY CHECK(id = 1), name TEXT NOT NULL)"
            )
            row = connection.execute(
                "SELECT name FROM workspace_model WHERE id = 1"
            ).fetchone()
        if row is not None:
            name = row[0]
            if (
                isinstance(name, str)
                and 0 < len(name) <= 200
                and not any(ord(c) < 32 for c in name)
            ):
                self.provider.model = name

    def catalog(self) -> dict[str, object]:
        with self._lock:
            installed = self.provider.installed_models()

            def inspect(item: dict[str, object]):
                key = (item["name"], item["digest"])
                supported = self._capabilities.get(key)
                if supported is None:
                    try:
                        supported = self.provider.supports_completion(item["name"])
                    except IntentProviderError:
                        # A failed inspection must not hide healthy models or
                        # become a cached negative result on the next refresh.
                        return item, key, None
                return item, key, supported

            with ThreadPoolExecutor(max_workers=8) as pool:
                inspected = list(pool.map(inspect, installed))
            self._capabilities = {
                key: supported
                for _, key, supported in inspected
                if supported is not None
            }
            models = [
                {
                    "name": item["name"],
                    "display_name": item["name"],
                    "size_bytes": item["size_bytes"],
                }
                for item, _, supported in inspected
                if supported
            ]
            return {
                "schema_version": 1,
                "active_model": self.provider.model,
                "busy": bool(self.workspace.store.list_runs(limit=1, active_only=True)),
                "models": sorted(models, key=lambda item: item["name"].casefold()),
                "errors": [
                    {"name": item["name"], "code": "model_inspection_unavailable"}
                    for item, _, supported in inspected
                    if supported is None
                ],
            }

    def select(self, name: str) -> dict[str, object]:
        catalog = self.catalog()
        if name not in {item["name"] for item in catalog["models"]}:
            raise ValueError("model_not_available")

        def apply() -> dict[str, object]:
            with self._lock, sqlite3.connect(self.state_path) as connection:
                connection.execute(
                    "INSERT INTO workspace_model(id, name) VALUES(1, ?) ON CONFLICT(id) DO UPDATE SET name = excluded.name",
                    (name,),
                )
                connection.commit()
                self.provider.model = name
            return {"schema_version": 1, "active_model": name}

        return self.workspace.configure_model(apply)

    def status(self) -> dict[str, object]:
        with self._lock:
            health = self.provider.health()
            if health.available:
                status = {"state": "ready", "installed": True}
            elif (
                self.provider.model == self.default_model
                and self.default_status is not None
            ):
                try:
                    status = dict(self.default_status())
                except Exception:
                    status = {
                        "state": "error",
                        "installed": False,
                        "reason": "model_status_unavailable",
                    }
            else:
                status = {
                    "state": "unavailable",
                    "installed": False,
                    "reason": health.reason,
                }
            return {**status, "name": self.provider.model}
