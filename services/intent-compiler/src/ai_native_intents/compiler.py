"""Model-neutral intent compilation pipeline."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from .catalog import OperationCatalog, OperationDefinition
from .clarification import ClarificationPolicy
from .context import ContextResolver
from .contracts import (
    CompilationResult,
    CompilationState,
    ModelHistoryMessage,
    ModelRequest,
    TaskContext,
)
from .planner import IntentPlanner
from .provider import (
    IntentModelProvider,
    IntentProviderResponseError,
    IntentProviderUnavailableError,
)
from .schema import MODEL_INSTRUCTIONS, intent_output_schema
from .validation import IntentValidationError, IntentValidator


class IntentCompiler:
    def __init__(
        self,
        provider: IntentModelProvider,
        *,
        operation_source: Callable[[], Iterable[OperationDefinition]],
    ) -> None:
        self.provider = provider
        self.operation_source = operation_source
        self.validator = IntentValidator()
        self.context_resolver = ContextResolver()
        self.clarification_policy = ClarificationPolicy()
        self.planner = IntentPlanner()

    def compile_and_plan(
        self, text: str, *, context: TaskContext | None = None
    ) -> CompilationResult:
        context = context or TaskContext()
        try:
            normalized = self._user_text(text)
        except (TypeError, ValueError):
            return self._rejected(context)
        try:
            payload = self.provider.compile(
                self.model_request(normalized, context=context)
            )
        except IntentProviderResponseError:
            return self._rejected(context)
        except IntentProviderUnavailableError:
            return self._rejected(context, diagnostic="provider_unavailable")
        except Exception:
            return self._rejected(context, diagnostic="provider_unavailable")
        return self.compile_payload(payload, text=normalized, context=context)

    def model_request(
        self,
        text: str,
        *,
        context: TaskContext | None = None,
        history: tuple[ModelHistoryMessage, ...] = (),
        allowed_operations: tuple[str, ...] | None = None,
    ) -> ModelRequest:
        context = context or TaskContext()
        normalized = self._user_text(text)
        definitions = OperationCatalog(self.operation_source()).select(
            allowed_operations
        )
        return ModelRequest(
            user_text=normalized,
            locale=context.locale,
            context=context.for_model(),
            output_schema=intent_output_schema(definitions),
            instructions=MODEL_INSTRUCTIONS,
            history=history,
            allowed_operations=allowed_operations,
            operation_definitions=definitions,
        )

    def compile_payload(
        self,
        payload: object,
        *,
        text: str,
        context: TaskContext | None = None,
        allowed_operations: tuple[str, ...] | None = None,
    ) -> CompilationResult:
        """Validate and plan a model route without invoking the model again."""
        context = context or TaskContext()
        try:
            normalized = self._user_text(text)
        except (TypeError, ValueError):
            return self._rejected(context)
        try:
            if not isinstance(payload, Mapping):
                raise IntentValidationError("intent provider returned a non-object")
            definitions = OperationCatalog(self.operation_source()).select(
                allowed_operations
            )
            intent = self.validator.parse(
                payload,
                user_text=normalized,
                operation_definitions=definitions,
            )
            intent, missing_context = self.context_resolver.resolve(intent, context)
            question = self.clarification_policy.question(
                intent,
                missing_context=missing_context,
                context=context,
            )
            plan = self.planner.plan(
                intent,
                operation_definitions=definitions,
                clarification_question=question,
            )
            diagnostics = (
                ("missing_capabilities",)
                if plan.state is CompilationState.UNAVAILABLE
                else ()
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
