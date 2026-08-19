"""Strict semantic validation after an untrusted model response."""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlparse
from uuid import uuid4

from .contracts import IntentValue, OperationIntent, OperationKind, UserIntent


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TOP_LEVEL_KEYS = {"schema_version", "language", "summary", "confidence", "operations"}
_OPERATION_KEYS = {"id", "kind", "arguments", "depends_on", "evidence"}
_ARGUMENT_SCHEMAS: dict[OperationKind, dict[str, str]] = {
    OperationKind.SEARCH_DOCUMENTS: {
        "text": "string",
        "extensions": "strings",
        "languages": "strings",
        "volume_ids": "strings",
        "name_terms": "strings",
    },
    OperationKind.FIND_APPLICATION: {"query": "string"},
    OperationKind.PLAN_WEB_SEARCH: {"query": "string", "engine": "string"},
    OperationKind.PLAN_OPEN_URL: {"url": "url"},
    OperationKind.SAVE_RESULTS: {"results_from": "reference", "title": "string"},
    OperationKind.COPY_RESULTS: {
        "results_from": "reference",
        "destination_role": "destination_role",
        "destination_name": "string",
        "destination_ref": "reference",
    },
}


class IntentValidationError(ValueError):
    pass


class IntentValidator:
    def parse(self, payload: Mapping[str, object], *, user_text: str) -> UserIntent:
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
            self._operation(item, user_text=user_text, ordinal=ordinal)
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
        self,
        value: object,
        *,
        user_text: str,
        ordinal: int,
    ) -> OperationIntent:
        if not isinstance(value, Mapping):
            raise IntentValidationError(f"operation {ordinal} must be an object")
        self._exact_keys(value, _OPERATION_KEYS, f"operation {ordinal}")
        operation_id = self._string(value.get("id"), "operation id", maximum=64)
        if not _IDENTIFIER.fullmatch(operation_id):
            raise IntentValidationError(f"invalid operation id: {operation_id}")
        try:
            kind = OperationKind(self._string(value.get("kind"), "operation kind", maximum=64))
        except ValueError as error:
            raise IntentValidationError("unsupported operation kind") from error
        arguments = self._arguments(kind, value.get("arguments"))
        if kind is OperationKind.PLAN_OPEN_URL:
            url = arguments.get("url")
            if not isinstance(url, str) or url.casefold() not in user_text.casefold():
                raise IntentValidationError("URL must occur verbatim in the user message")
        depends_on = self._string_list(value.get("depends_on"), "depends_on", maximum=12)
        evidence = self._string_list(value.get("evidence"), "evidence", maximum=12)
        if not evidence:
            raise IntentValidationError(f"operation {operation_id} has no grounding evidence")
        normalized_text = user_text.casefold()
        for quote in evidence:
            if quote.casefold() not in normalized_text:
                raise IntentValidationError(
                    f"operation {operation_id} contains evidence absent from the user message"
                )
        return OperationIntent(operation_id, kind, arguments, depends_on, evidence)

    def _arguments(self, kind: OperationKind, value: object) -> dict[str, IntentValue]:
        if not isinstance(value, Mapping):
            raise IntentValidationError(f"arguments for {kind.value} must be an object")
        schema = _ARGUMENT_SCHEMAS[kind]
        unknown = set(value) - set(schema)
        if unknown:
            raise IntentValidationError(
                f"unsupported arguments for {kind.value}: {sorted(unknown)}"
            )
        result: dict[str, IntentValue] = {}
        for key, raw in value.items():
            argument_type = schema[key]
            if argument_type in {"string", "reference"}:
                item = self._string(raw, key, maximum=2_000)
                if key == "engine" and item not in {"duckduckgo", "google"}:
                    raise IntentValidationError("unsupported search engine")
                result[key] = item
            elif argument_type == "strings":
                items = self._string_list(raw, key, maximum=100)
                if key == "extensions":
                    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_-]{0,15}", item) for item in items):
                        raise IntentValidationError("extensions must be simple suffixes without dots")
                    items = tuple(item.casefold() for item in items)
                result[key] = items
            elif argument_type == "destination_role":
                role = self._string(raw, key, maximum=32)
                if role not in {"desktop", "documents", "downloads"}:
                    raise IntentValidationError("unsupported destination_role")
                result[key] = role
            elif argument_type == "url":
                url = self._string(raw, key, maximum=2_000)
                parsed = urlparse(url)
                if (
                    parsed.scheme not in {"http", "https"}
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                ):
                    raise IntentValidationError("only credential-free HTTP(S) URLs are allowed")
                result[key] = parsed.geturl()
        destination_name = result.get("destination_name")
        if isinstance(destination_name, str) and (
            destination_name in {".", ".."}
            or "/" in destination_name
            or "\\" in destination_name
        ):
            raise IntentValidationError("destination_name must be one directory name")
        return result

    @staticmethod
    def _validate_graph(operations: tuple[OperationIntent, ...]) -> None:
        seen: set[str] = set()
        output_kinds: dict[str, OperationKind] = {}
        all_ids = [operation.operation_id for operation in operations]
        if len(set(all_ids)) != len(all_ids):
            raise IntentValidationError("operation ids must be unique")
        for operation in operations:
            for dependency in operation.depends_on:
                if dependency not in seen:
                    raise IntentValidationError(
                        f"dependency must reference an earlier operation: {dependency}"
                    )
            for key in ("results_from", "destination_ref"):
                reference = operation.arguments.get(key)
                if not isinstance(reference, str):
                    continue
                if reference in {"context.active_results", "context.last_destination"}:
                    continue
                if reference not in seen:
                    raise IntentValidationError(
                        f"{key} must reference trusted context or an earlier operation"
                    )
                if key == "results_from" and output_kinds[reference] not in {
                    OperationKind.SEARCH_DOCUMENTS,
                    OperationKind.SAVE_RESULTS,
                }:
                    raise IntentValidationError("results_from must reference a result-producing operation")
                if key == "destination_ref":
                    raise IntentValidationError("destination_ref must use trusted task context")
            seen.add(operation.operation_id)
            output_kinds[operation.operation_id] = operation.kind

    @staticmethod
    def _evidence_coverage(operations: tuple[OperationIntent, ...]) -> float:
        return sum(bool(operation.evidence) for operation in operations) / len(operations)

    @staticmethod
    def _exact_keys(value: Mapping[str, object], allowed: set[str], label: str) -> None:
        missing = allowed - set(value)
        unknown = set(value) - allowed
        if missing:
            raise IntentValidationError(f"{label} misses fields: {sorted(missing)}")
        if unknown:
            raise IntentValidationError(f"{label} has unknown fields: {sorted(unknown)}")

    @staticmethod
    def _string(value: object, label: str, *, maximum: int) -> str:
        if not isinstance(value, str):
            raise IntentValidationError(f"{label} must be a string")
        value = value.strip()
        if not value or len(value) > maximum or any(ord(character) < 32 for character in value):
            raise IntentValidationError(f"{label} has invalid length or control characters")
        return value

    @classmethod
    def _string_list(cls, value: object, label: str, *, maximum: int) -> tuple[str, ...]:
        if not isinstance(value, list) or len(value) > maximum:
            raise IntentValidationError(f"{label} must be a bounded string array")
        return tuple(cls._string(item, label, maximum=300) for item in value)
