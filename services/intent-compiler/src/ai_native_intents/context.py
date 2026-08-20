"""Resolve only symbolic references against trusted task state."""

from __future__ import annotations

from dataclasses import replace
import os
import sqlite3
from pathlib import Path
from threading import RLock

from .contracts import OperationIntent, OperationKind, TaskContext, UserIntent


class ContextResolver:
    def resolve(self, intent: UserIntent, context: TaskContext) -> tuple[UserIntent, tuple[str, ...]]:
        missing: list[str] = []
        resolved_operations: list[OperationIntent] = []
        kinds = {operation.operation_id: operation.kind for operation in intent.operations}
        for operation in intent.operations:
            arguments = dict(operation.arguments)
            if operation.kind in {OperationKind.SAVE_RESULTS, OperationKind.COPY_RESULTS}:
                if "results_from" not in arguments:
                    producers = [
                        dependency
                        for dependency in operation.depends_on
                        if kinds.get(dependency)
                        in {OperationKind.SEARCH_DOCUMENTS, OperationKind.SAVE_RESULTS}
                    ]
                    if len(producers) == 1:
                        arguments["results_from"] = producers[0]
            directory_name = arguments.get("directory_name")
            if (
                isinstance(directory_name, str)
                and directory_name.casefold() not in intent.original_text.casefold()
            ):
                arguments.pop("directory_name")
            for key, value in list(arguments.items()):
                if value == "context.active_results":
                    if context.active_collection_id is None:
                        missing.append("active_results")
                    else:
                        arguments[key] = context.active_collection_id
                elif value == "context.last_destination":
                    if context.last_destination is None:
                        missing.append("last_destination")
                    else:
                        arguments[key] = context.last_destination
            resolved_operations.append(replace(operation, arguments=arguments))
        return replace(intent, operations=tuple(resolved_operations)), tuple(dict.fromkeys(missing))


class TaskContextStore:
    """Small thread-safe, server-owned context for the active panel task."""

    def __init__(self, *, locale: str = "ru", database: Path | None = None) -> None:
        self._lock = RLock()
        self.database = None if database is None else Path(database)
        self._context = TaskContext(locale=locale)
        if self.database is not None:
            self._initialize_database(locale)

    def snapshot(self) -> TaskContext:
        with self._lock:
            return self._context

    def set_active_results(self, collection_id: str | None) -> TaskContext:
        with self._lock:
            self._context = replace(
                self._context,
                active_collection_id=self._trusted_value(collection_id, "collection_id"),
            )
            self._persist()
            return self._context

    def set_last_destination(self, destination: str | None) -> TaskContext:
        with self._lock:
            self._context = replace(
                self._context,
                last_destination=self._trusted_value(destination, "destination"),
            )
            self._persist()
            return self._context

    def clear(self) -> TaskContext:
        with self._lock:
            self._context = TaskContext(locale=self._context.locale)
            self._persist()
            return self._context

    def _initialize_database(self, locale: str) -> None:
        assert self.database is not None
        if self.database.exists() and self.database.is_symlink():
            raise ValueError("task context database cannot be a symlink")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS task_context (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    active_collection_id TEXT,
                    last_destination TEXT,
                    locale TEXT NOT NULL
                )"""
            )
            row = connection.execute(
                "SELECT active_collection_id, last_destination FROM task_context WHERE singleton = 1"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO task_context(singleton, locale) VALUES (1, ?)",
                    (locale,),
                )
            else:
                self._context = TaskContext(row[0], row[1], locale)
                connection.execute(
                    "UPDATE task_context SET locale = ? WHERE singleton = 1", (locale,)
                )
        if os.name == "posix":
            os.chmod(self.database, 0o600)

    def _persist(self) -> None:
        if self.database is None:
            return
        with self._connect() as connection:
            connection.execute(
                """UPDATE task_context SET active_collection_id = ?,
                   last_destination = ?, locale = ? WHERE singleton = 1""",
                (
                    self._context.active_collection_id,
                    self._context.last_destination,
                    self._context.locale,
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        assert self.database is not None
        connection = sqlite3.connect(self.database, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _trusted_value(value: str | None, label: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError(f"{label} must be a string or None")
        value = value.strip()
        if not value or len(value) > 4_096 or any(ord(character) < 32 for character in value):
            raise ValueError(f"{label} is invalid")
        return value
