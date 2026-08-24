"""Persistent capability inventory with dependency-aware module states."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .contracts import (
    CapabilityProvider,
    IntentRouteDescriptor,
    ModuleManifest,
    ModuleState,
    RegisteredModule,
    SyncIssue,
    SyncReport,
)
from .database import RegistryDatabase
from .manifest import (
    MANIFEST_FILENAME,
    ManifestValidationError,
    load_manifest,
    manifest_to_dict,
    validate_manifest,
)


CORE_API_VERSION = "1"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CapabilityRegistry:
    def __init__(self, database_path: Path) -> None:
        self.database = RegistryDatabase(database_path)

    def sync(self, roots: list[Path]) -> SyncReport:
        manifest_paths = self._find_manifests(roots)
        registered = 0
        updated = 0
        issues: list[SyncIssue] = []
        seen_module_ids: set[str] = set()

        for path in manifest_paths:
            try:
                manifest = load_manifest(path)
            except (OSError, ManifestValidationError) as error:
                issues.append(SyncIssue(path=str(path), error=str(error)))
                self._mark_invalid_path(path, str(error))
                continue
            if manifest.module_id in seen_module_ids:
                issues.append(
                    SyncIssue(
                        path=str(path),
                        error=f"duplicate module_id in this sync: {manifest.module_id}",
                    )
                )
                continue
            seen_module_ids.add(manifest.module_id)
            try:
                was_update = self._upsert(path, manifest)
            except ManifestValidationError as error:
                issues.append(SyncIssue(path=str(path), error=str(error)))
                continue
            if was_update:
                updated += 1
            else:
                registered += 1

        self.refresh_states()
        return SyncReport(
            scanned=len(manifest_paths),
            registered=registered,
            updated=updated,
            issues=tuple(issues),
        )

    def refresh_states(self) -> None:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM modules").fetchall()
            manifests = {
                str(row["module_id"]): validate_manifest(json.loads(str(row["manifest_json"])))
                for row in rows
            }
            row_by_id = {str(row["module_id"]): row for row in rows}
            cycles = self._cycle_members(
                {module_id: manifest.dependencies for module_id, manifest in manifests.items()}
            )
            states: dict[str, ModuleState] = {}
            reasons: dict[str, str | None] = {}

            for module_id, manifest in manifests.items():
                row = row_by_id[module_id]
                if not bool(row["manifest_valid"]):
                    states[module_id] = ModuleState.UNAVAILABLE
                    reasons[module_id] = str(row["state_reason"] or "invalid_manifest")
                elif bool(row["quarantined"]):
                    states[module_id] = ModuleState.QUARANTINED
                    reasons[module_id] = str(row["quarantine_reason"] or "quarantined")
                elif not bool(row["desired_enabled"]):
                    states[module_id] = (
                        ModuleState.DISABLED
                        if bool(row["user_configured"])
                        else ModuleState.INSTALLED
                    )
                    reasons[module_id] = "disabled_by_user" if bool(row["user_configured"]) else None
                elif manifest.core_api != CORE_API_VERSION:
                    states[module_id] = ModuleState.UNAVAILABLE
                    reasons[module_id] = f"incompatible_core_api:{manifest.core_api}"
                elif module_id in cycles:
                    states[module_id] = ModuleState.UNAVAILABLE
                    reasons[module_id] = "dependency_cycle"
                else:
                    missing = next(
                        (dependency for dependency in manifest.dependencies if dependency not in manifests),
                        None,
                    )
                    if missing is not None:
                        states[module_id] = ModuleState.UNAVAILABLE
                        reasons[module_id] = f"missing_dependency:{missing}"
                    else:
                        states[module_id] = ModuleState.ENABLED
                        reasons[module_id] = None

            changed = True
            while changed:
                changed = False
                for module_id, manifest in manifests.items():
                    if states[module_id] is not ModuleState.ENABLED:
                        continue
                    blocked = next(
                        (
                            dependency
                            for dependency in manifest.dependencies
                            if states.get(dependency) is not ModuleState.ENABLED
                        ),
                        None,
                    )
                    if blocked is not None:
                        states[module_id] = ModuleState.UNAVAILABLE
                        reasons[module_id] = f"dependency_not_enabled:{blocked}"
                        changed = True

            connection.executemany(
                "UPDATE modules SET state = ?, state_reason = ? WHERE module_id = ?",
                [
                    (states[module_id].value, reasons[module_id], module_id)
                    for module_id in manifests
                ],
            )

    def set_enabled(self, module_id: str, enabled: bool) -> RegisteredModule:
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE modules
                SET desired_enabled = ?, user_configured = 1
                WHERE module_id = ?
                """,
                (enabled, module_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown module: {module_id}")
        self.refresh_states()
        return self.get_module(module_id)

    def quarantine(self, module_id: str, reason: str) -> RegisteredModule:
        reason = reason.strip()
        if not reason or len(reason) > 500:
            raise ValueError("quarantine reason must contain from 1 to 500 characters")
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE modules
                SET quarantined = 1, quarantine_reason = ?
                WHERE module_id = ?
                """,
                (reason, module_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown module: {module_id}")
        self.refresh_states()
        return self.get_module(module_id)

    def clear_quarantine(self, module_id: str) -> RegisteredModule:
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE modules
                SET quarantined = 0, quarantine_reason = NULL
                WHERE module_id = ?
                """,
                (module_id,),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown module: {module_id}")
        self.refresh_states()
        return self.get_module(module_id)

    def get_module(self, module_id: str) -> RegisteredModule:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM modules WHERE module_id = ?", (module_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown module: {module_id}")
        return self._from_row(row)

    def list_modules(self) -> list[RegisteredModule]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM modules ORDER BY module_id").fetchall()
        return [self._from_row(row) for row in rows]

    def available_capabilities(self) -> list[str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT capability_id
                FROM module_capabilities AS c
                JOIN modules AS m ON m.module_id = c.module_id
                WHERE m.state = ?
                ORDER BY capability_id
                """,
                (ModuleState.ENABLED.value,),
            ).fetchall()
        return [str(row["capability_id"]) for row in rows]

    def intent_routes(self) -> tuple[IntentRouteDescriptor, ...]:
        """Publish routes only from modules that are currently enabled."""
        return tuple(
            route
            for module in self.list_modules()
            if module.state is ModuleState.ENABLED
            for route in module.manifest.intent_routes
        )

    def providers(
        self,
        capability_id: str,
        *,
        available_only: bool = True,
    ) -> list[CapabilityProvider]:
        sql = """
            SELECT c.capability_id, m.module_id, m.module_version, m.state
            FROM module_capabilities AS c
            JOIN modules AS m ON m.module_id = c.module_id
            WHERE c.capability_id = ?
        """
        parameters: list[object] = [capability_id]
        if available_only:
            sql += " AND m.state = ?"
            parameters.append(ModuleState.ENABLED.value)
        sql += " ORDER BY m.module_id"
        with self.database.connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [
            CapabilityProvider(
                capability_id=str(row["capability_id"]),
                module_id=str(row["module_id"]),
                module_version=str(row["module_version"]),
                state=ModuleState(str(row["state"])),
            )
            for row in rows
        ]

    def _upsert(self, path: Path, manifest: ModuleManifest) -> bool:
        path_text = str(path.resolve(strict=True))
        manifest_payload = manifest_to_dict(manifest)
        manifest_json = json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True)
        manifest_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
        timestamp = _now()
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT module_id FROM modules WHERE module_id = ?", (manifest.module_id,)
            ).fetchone()
            conflicting_path = connection.execute(
                "SELECT module_id FROM modules WHERE manifest_path = ? AND module_id != ?",
                (path_text, manifest.module_id),
            ).fetchone()
            if conflicting_path is not None:
                raise ManifestValidationError(
                    f"manifest path already belongs to {conflicting_path['module_id']}"
                )
            connection.execute(
                """
                INSERT INTO modules(
                    module_id, manifest_json, manifest_path, manifest_hash,
                    manifest_valid, module_version, core_api, desired_enabled,
                    user_configured, quarantined, state, last_seen_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, 0, 0, ?, ?)
                ON CONFLICT(module_id) DO UPDATE SET
                    manifest_json = excluded.manifest_json,
                    manifest_path = excluded.manifest_path,
                    manifest_hash = excluded.manifest_hash,
                    manifest_valid = 1,
                    module_version = excluded.module_version,
                    core_api = excluded.core_api,
                    state_reason = NULL,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    manifest.module_id,
                    manifest_json,
                    path_text,
                    manifest_hash,
                    manifest.module_version,
                    manifest.core_api,
                    manifest.default_enabled,
                    ModuleState.INSTALLED.value,
                    timestamp,
                ),
            )
            connection.execute(
                "DELETE FROM module_capabilities WHERE module_id = ?", (manifest.module_id,)
            )
            connection.executemany(
                "INSERT INTO module_capabilities(module_id, capability_id) VALUES (?, ?)",
                [(manifest.module_id, capability) for capability in manifest.capabilities],
            )
        return existing is not None

    def _mark_invalid_path(self, path: Path, error: str) -> None:
        path_text = str(path.resolve(strict=False))
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE modules
                SET manifest_valid = 0, state = ?, state_reason = ?, last_seen_at = ?
                WHERE manifest_path = ?
                """,
                (ModuleState.UNAVAILABLE.value, f"invalid_manifest:{error}", _now(), path_text),
            )

    @staticmethod
    def _find_manifests(roots: list[Path]) -> list[Path]:
        found: set[Path] = set()
        for root in roots:
            root = root.expanduser()
            if root.is_file():
                found.add(root.resolve(strict=True))
            elif root.is_dir():
                found.update(path.resolve(strict=True) for path in root.rglob(MANIFEST_FILENAME))
            else:
                raise FileNotFoundError(root)
        return sorted(found)

    @staticmethod
    def _cycle_members(graph: dict[str, tuple[str, ...]]) -> set[str]:
        visited: set[str] = set()
        active: list[str] = []
        active_set: set[str] = set()
        cycles: set[str] = set()

        def visit(node: str) -> None:
            if node in active_set:
                cycles.update(active[active.index(node) :])
                return
            if node in visited:
                return
            active.append(node)
            active_set.add(node)
            for dependency in graph.get(node, ()):
                if dependency in graph:
                    visit(dependency)
            active.pop()
            active_set.remove(node)
            visited.add(node)

        for module_id in graph:
            visit(module_id)
        return cycles

    @staticmethod
    def _from_row(row: sqlite3.Row) -> RegisteredModule:
        manifest = validate_manifest(json.loads(str(row["manifest_json"])))
        return RegisteredModule(
            manifest=manifest,
            manifest_path=str(row["manifest_path"]),
            desired_enabled=bool(row["desired_enabled"]),
            state=ModuleState(str(row["state"])),
            state_reason=None if row["state_reason"] is None else str(row["state_reason"]),
            quarantined=bool(row["quarantined"]),
            last_seen_at=str(row["last_seen_at"]),
        )
