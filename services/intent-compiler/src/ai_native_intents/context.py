"""Resolve only symbolic references against trusted task state."""

from __future__ import annotations

from dataclasses import replace

from .contracts import OperationIntent, TaskContext, UserIntent


class ContextResolver:
    def resolve(self, intent: UserIntent, context: TaskContext) -> tuple[UserIntent, tuple[str, ...]]:
        missing: list[str] = []
        resolved_operations: list[OperationIntent] = []
        for operation in intent.operations:
            arguments = dict(operation.arguments)
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
