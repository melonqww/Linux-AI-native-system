"""Model-neutral intent compilation pipeline."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from typing import Final

from .clarification import ClarificationPolicy
from .context import ContextResolver
from .contracts import CompilationResult, CompilationState, ModelRequest, TaskContext
from .planner import IntentPlanner
from .provider import IntentModelProvider
from .schema import INTENT_OUTPUT_SCHEMA, MODEL_INSTRUCTIONS
from .validation import IntentValidationError, IntentValidator


_CORE_CAPABILITIES: Final = frozenset({"documents.query.search"})


class IntentCompiler:
    def __init__(
        self,
        provider: IntentModelProvider,
        *,
        capability_source: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self.provider = provider
        self.capability_source = capability_source or (lambda: ())
        self.validator = IntentValidator()
        self.context_resolver = ContextResolver()
        self.clarification_policy = ClarificationPolicy()
        self.planner = IntentPlanner()

    def compile_and_plan(self, text: str, *, context: TaskContext | None = None) -> CompilationResult:
        context = context or TaskContext()
        try:
            normalized = self._user_text(text)
        except (TypeError, ValueError):
            return self._rejected(context)
        try:
            payload = self.provider.compile(
                ModelRequest(
                    user_text=normalized,
                    locale=context.locale,
                    context=context.for_model(),
                    output_schema=deepcopy(INTENT_OUTPUT_SCHEMA),
                    instructions=MODEL_INSTRUCTIONS,
                )
            )
        except Exception:
            return self._rejected(context, diagnostic="provider_unavailable")
        try:
            if not isinstance(payload, Mapping):
                raise IntentValidationError("intent provider returned a non-object")
            intent = self.validator.parse(payload, user_text=normalized)
            intent, missing_context = self.context_resolver.resolve(intent, context)
            question = self.clarification_policy.question(
                intent,
                missing_context=missing_context,
                context=context,
            )
            available = set(self.capability_source()) | set(_CORE_CAPABILITIES)
            plan = self.planner.plan(
                intent,
                available_capabilities=available,
                clarification_question=question,
            )
            diagnostics = (
                ("missing_capabilities",) if plan.state is CompilationState.UNAVAILABLE else ()
            )
            return CompilationResult(plan.state, intent, plan, question, diagnostics)
        except (IntentValidationError, TypeError, ValueError, RuntimeError):
            return self._rejected(context)

    @staticmethod
    def _rejected(
        context: TaskContext,
        *,
        diagnostic: str = "intent_rejected",
    ) -> CompilationResult:
        question = (
            "Не удалось надёжно понять запрос. Сформулируйте, пожалуйста, точнее."
            if context.locale.casefold().startswith("ru")
            else "The request could not be understood reliably. Please make it more specific."
        )
        return CompilationResult(
            CompilationState.NEEDS_CLARIFICATION,
            None,
            None,
            question,
            (diagnostic,),
        )

    @staticmethod
    def _user_text(text: str) -> str:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        text = text.strip()
        if not text or len(text) > 4_000:
            raise ValueError("text must contain from 1 to 4000 characters")
        if any(ord(character) < 32 and character not in "\n\t" for character in text):
            raise ValueError("text contains control characters")
        return text
