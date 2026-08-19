"""Resolve only symbolic references against trusted task state."""

from __future__ import annotations

from dataclasses import replace
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

    def __init__(self, *, locale: str = "ru") -> None:
        self._lock = RLock()
        self._context = TaskContext(locale=locale)

    def snapshot(self) -> TaskContext:
        with self._lock:
            return self._context

    def set_active_results(self, collection_id: str | None) -> TaskContext:
        with self._lock:
            self._context = replace(
                self._context,
                active_collection_id=self._trusted_value(collection_id, "collection_id"),
            )
            return self._context

    def set_last_destination(self, destination: str | None) -> TaskContext:
        with self._lock:
            self._context = replace(
                self._context,
                last_destination=self._trusted_value(destination, "destination"),
            )
            return self._context

    def clear(self) -> TaskContext:
        with self._lock:
            self._context = TaskContext(locale=self._context.locale)
            return self._context

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
