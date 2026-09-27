"""System-owned completeness and confidence policy."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .catalog import OperationDefinition
from .contracts import TaskContext, UserIntent


class ClarificationPolicy:
    def question(
        self,
        intent: UserIntent,
        *,
        operation_definitions: Iterable[OperationDefinition] = (),
        missing_context: tuple[str, ...],
        context: TaskContext,
        requires_write_confidence: bool = True,
    ) -> str | None:
        russian = context.locale.casefold().startswith("ru")
        # A small model's self-reported confidence is advisory for validated
        # read-only operations. It must not veto a safe search by itself.
        # Writes retain the stricter confidence gate.
        if requires_write_confidence and intent.grounded_confidence < 0.5:
            return (
                "Уточните, пожалуйста, что именно нужно найти или сделать."
                if russian
                else "Please clarify what should be found or done."
            )
        if "active_results" in missing_context:
            return (
                "Какие именно результаты нужно использовать? Сначала выполните поиск или выберите коллекцию."
                if russian
                else "Which results should be used? Run a search or select a collection first."
            )
        if "last_destination" in missing_context:
            return (
                "Куда именно нужно сохранить или скопировать результаты?"
                if russian
                else "Where should the results be saved or copied?"
            )
        definitions = {item.operation: item for item in operation_definitions}
        for operation in intent.operations:
            definition = definitions.get(operation.kind)
            if definition is None:
                continue
            arguments = operation.arguments
            properties = definition.input_schema["properties"]
            search_fields = tuple(
                name for name, schema in properties.items()
                if schema.get("semanticRole") in {
                    "content_text", "file_extensions", "filename_terms"
                }
            )
            if (
                search_fields
                and not any(arguments.get(name) for name in search_fields)
                and not self._has_constrained_selection(arguments, definition)
            ):
                return "Что именно нужно найти?" if russian else "What should be found?"
        return None

    @staticmethod
    def _has_constrained_selection(
        arguments: Mapping[str, object], definition: OperationDefinition | None
    ) -> bool:
        if definition is None:
            return False
        properties = definition.input_schema["properties"]
        for name, schema in properties.items():
            value = arguments.get(name)
            if not schema.get("explicitRequestReview") or not isinstance(value, str):
                continue
            requirements = schema.get("valueRequires", {}).get(value)
            if requirements and all(
                arguments.get(peer) in allowed
                for peer, allowed in requirements.items()
            ):
                return True
        return False
