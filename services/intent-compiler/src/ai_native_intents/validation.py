"""Strict semantic validation after an untrusted model response."""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlparse
from uuid import uuid4

from .catalog import OperationCatalog, OperationDefinition
from .contracts import IntentValue, OperationIntent, UserIntent

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TOP_LEVEL_KEYS = {"schema_version", "language", "summary", "confidence", "operations"}
_OPERATION_KEYS = {"id", "kind", "arguments", "depends_on", "evidence"}


class IntentValidationError(ValueError):
    """Rejected untrusted intent with a stable, non-sensitive developer code."""

    def __init__(self, message: str, *, code: str = "schema_validation") -> None:
        super().__init__(message)
        self.code = code


class IntentValidator:
    def parse(
        self,
        payload: Mapping[str, object],
        *,
        user_text: str,
        operation_definitions: tuple[OperationDefinition, ...],
    ) -> UserIntent:
        catalog = OperationCatalog(operation_definitions)
        self._exact_keys(payload, _TOP_LEVEL_KEYS, "intent")
        if payload.get("schema_version") != 1:
            raise IntentValidationError("unsupported intent schema_version")
        language = self._string(payload.get("language"), "language", maximum=16)
        summary = self._string(payload.get("summary"), "summary", maximum=500)
        confidence = payload.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= float(confidence) <= 1
        ):
            raise IntentValidationError("confidence must be a number from 0 to 1")
        raw_operations = payload.get("operations")
        if not isinstance(raw_operations, list) or not 1 <= len(raw_operations) <= 12:
            raise IntentValidationError("operations must contain from 1 to 12 items")
        operations = tuple(
            self._operation(item, user_text=user_text, ordinal=ordinal, catalog=catalog)
            for ordinal, item in enumerate(raw_operations)
        )
        self._validate_graph(operations)
        grounded = float(confidence) * self._evidence_coverage(operations)
        return UserIntent(
            intent_id=str(uuid4()),
            original_text=user_text,
            language=language,
            summary=summary,
            model_confidence=float(confidence),
            grounded_confidence=round(grounded, 6),
            operations=operations,
        )

    def _operation(
        self, value: object, *, user_text: str, ordinal: int, catalog: OperationCatalog
    ) -> OperationIntent:
        if not isinstance(value, Mapping):
            raise IntentValidationError(f"operation {ordinal} must be an object")
        self._exact_keys(value, _OPERATION_KEYS, f"operation {ordinal}")
        operation_id = self._string(value.get("id"), "operation id", maximum=64)
        if not _IDENTIFIER.fullmatch(operation_id):
            raise IntentValidationError(f"invalid operation id: {operation_id}")
        kind = self._string(value.get("kind"), "operation kind", maximum=64)
        try:
            definition = catalog.operation(kind)
        except ValueError as error:
            raise IntentValidationError("unsupported operation kind") from error
        raw_arguments = value.get("arguments")
        if not isinstance(raw_arguments, Mapping):
            raise IntentValidationError(f"arguments for {kind} must be an object")
        normalized = dict(raw_arguments)
        if kind == "search_documents":
            # Search mode is a derived execution detail, not model authority.
            # Always replace the model value so equivalent argument sets compile
            # identically even when a small model emits a contradictory mode.
            text = normalized.get("text")
            has_text = isinstance(text, str) and bool(text.strip())
            if isinstance(text, str) and not has_text:
                normalized.pop("text")
            has_metadata_filter = any(
                normalized.get(key) for key in ("extensions", "name_terms")
            )
            normalized["mode"] = (
                "hybrid"
                if has_text and has_metadata_filter
                else "content"
                if has_text
                else "metadata"
            )
        try:
            arguments = definition.validate_arguments(normalized)
        except (TypeError, ValueError) as error:
            raise IntentValidationError(str(error), code="invalid_arguments") from error
        self._validate_special_semantics(kind, arguments, user_text)
        depends_on = self._string_list(
            value.get("depends_on"), "depends_on", maximum=12
        )
        evidence = self._string_list(value.get("evidence"), "evidence", maximum=12)
        if not evidence:
            raise IntentValidationError(
                f"operation {operation_id} has no grounding evidence",
                code="ungrounded_operation",
            )
        normalized_text = user_text.casefold()
        for quote in evidence:
            if quote.casefold() not in normalized_text:
                raise IntentValidationError(
                    f"operation {operation_id} contains evidence absent from the user message",
                    code="ungrounded_operation",
                )
        return OperationIntent(operation_id, kind, arguments, depends_on, evidence)

    @staticmethod
    def _validate_special_semantics(
        kind: str, arguments: dict[str, IntentValue], user_text: str
    ) -> None:
        url = arguments.get("url")
        if isinstance(url, str):
            parsed = urlparse(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or url.casefold() not in user_text.casefold()
            ):
                raise IntentValidationError(
                    "URL must be a credential-free HTTP(S) URL present in the message"
                )
        destination_name = arguments.get("directory_name")
        if isinstance(destination_name, str) and (
            destination_name in {".", ".."}
            or "/" in destination_name
            or "\\" in destination_name
        ):
            raise IntentValidationError("directory_name must be one directory name")
        extensions = arguments.get("extensions")
        if isinstance(extensions, tuple):
            if any(
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_-]{0,15}", item)
                for item in extensions
            ):
                raise IntentValidationError(
                    "extensions must be simple suffixes without dots"
                )
            arguments["extensions"] = tuple(item.casefold() for item in extensions)
        if kind == "search_documents":
            mode = arguments.get("mode")
            text = arguments.get("text")
            if mode == "metadata" and text is not None:
                raise IntentValidationError("metadata search cannot contain text")
            if mode in {"content", "hybrid"} and not isinstance(text, str):
                raise IntentValidationError("content search requires text")

    @staticmethod
    def _validate_graph(operations: tuple[OperationIntent, ...]) -> None:
        seen: set[str] = set()
        all_ids = [operation.operation_id for operation in operations]
        if len(set(all_ids)) != len(all_ids):
            raise IntentValidationError("operation ids must be unique")
        for operation in operations:
            for dependency in operation.depends_on:
                if dependency not in seen:
                    raise IntentValidationError(
                        f"dependency must reference an earlier operation: {dependency}"
                    )
            reference = operation.arguments.get("results_from")
            if isinstance(reference, str) and reference != "context.active_results":
                if reference.startswith("context."):
                    raise IntentValidationError(
                        "results_from uses the wrong context reference"
                    )
                if reference not in seen:
                    raise IntentValidationError(
                        "results_from must reference trusted context or an earlier operation"
                    )
            seen.add(operation.operation_id)

    @staticmethod
    def _evidence_coverage(operations: tuple[OperationIntent, ...]) -> float:
        return sum(bool(operation.evidence) for operation in operations) / len(
            operations
        )

    @staticmethod
    def _exact_keys(value: Mapping[str, object], allowed: set[str], label: str) -> None:
        missing = allowed - set(value)
        unknown = set(value) - allowed
        if missing:
            raise IntentValidationError(f"{label} misses fields: {sorted(missing)}")
        if unknown:
            raise IntentValidationError(
                f"{label} has unknown fields: {sorted(unknown)}"
            )

    @staticmethod
    def _string(value: object, label: str, *, maximum: int) -> str:
        if not isinstance(value, str):
            raise IntentValidationError(f"{label} must be a string")
        value = value.strip()
        if (
            not value
            or len(value) > maximum
            or any(ord(character) < 32 for character in value)
        ):
            raise IntentValidationError(
                f"{label} has invalid length or control characters"
            )
        return value

    @classmethod
    def _string_list(
        cls, value: object, label: str, *, maximum: int
    ) -> tuple[str, ...]:
        if not isinstance(value, list) or len(value) > maximum:
            raise IntentValidationError(f"{label} must be a bounded string array")
        return tuple(cls._string(item, label, maximum=300) for item in value)
