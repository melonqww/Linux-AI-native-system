"""System-owned completeness and confidence policy."""

from __future__ import annotations

from .contracts import TaskContext, UserIntent


class ClarificationPolicy:
    def question(
        self,
        intent: UserIntent,
        *,
        missing_context: tuple[str, ...],
        context: TaskContext,
    ) -> str | None:
        russian = context.locale.casefold().startswith("ru")
        if intent.grounded_confidence < 0.5:
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
        for operation in intent.operations:
            arguments = operation.arguments
            if operation.kind == "search_documents" and not any(
                arguments.get(key) for key in ("text", "extensions", "name_terms")
            ):
                return "Что именно нужно найти?" if russian else "What should be found?"
            if operation.kind == "find_application" and not arguments.get("query"):
                return (
                    "Какое приложение найти?"
                    if russian
                    else "Which application should be found?"
                )
            if operation.kind == "plan_web_search" and not arguments.get("query"):
                return (
                    "Что искать в интернете?"
                    if russian
                    else "What should be searched on the web?"
                )
            if operation.kind == "save_results":
                if not arguments.get("results_from"):
                    return (
                        "Какие результаты сохранить?"
                        if russian
                        else "Which results should be saved?"
                    )
                if not arguments.get("title"):
                    return (
                        "Как назвать коллекцию?"
                        if russian
                        else "What should the collection be named?"
                    )
            if operation.kind == "copy_results":
                if not arguments.get("results_from"):
                    return (
                        "Какие результаты скопировать?"
                        if russian
                        else "Which results should be copied?"
                    )
                if not arguments.get("destination"):
                    return (
                        "Куда скопировать результаты?"
                        if russian
                        else "Where should results be copied?"
                    )
        return None
