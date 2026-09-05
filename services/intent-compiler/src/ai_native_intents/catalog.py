"""Immutable runtime catalog assembled from module metadata and trusted policy."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from .contracts import IntentValue, RiskClass


_OPERATION = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")
_PROPERTY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SCHEMA_KEYS = frozenset(
    {
        "type",
        "enum",
        "items",
        "description",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
    }
)
_SCALAR_TYPES = frozenset({"string", "integer", "number", "boolean"})


@dataclass(frozen=True, init=False)
class OperationDefinition:
    """One model-visible operation with policy supplied by trusted core code."""

    operation: str
    capability_id: str
    description: str
    examples: tuple[str, ...]
    risk: RiskClass
    approval_required: bool
    _schema_json: str

    def __init__(
        self,
        operation: str,
        capability_id: str,
        description: str,
        input_schema: Mapping[str, object],
        *,
        examples: Iterable[str] = (),
        risk: RiskClass = RiskClass.READ_ONLY,
        approval_required: bool = False,
    ) -> None:
        if not isinstance(operation, str) or not _OPERATION.fullmatch(operation):
            raise ValueError("operation identifier is invalid")
        if not isinstance(capability_id, str) or not _CAPABILITY.fullmatch(
            capability_id
        ):
            raise ValueError("capability identifier is invalid")
        if (
            not isinstance(description, str)
            or not description.strip()
            or len(description.strip()) > 500
        ):
            raise ValueError("operation description is invalid")
        if not isinstance(risk, RiskClass) or not isinstance(approval_required, bool):
            raise TypeError("operation policy is invalid")
        example_values = tuple(examples)
        if len(example_values) > 32 or any(
            not isinstance(item, str) or not item.strip() or len(item.strip()) > 300
            for item in example_values
        ):
            raise ValueError("operation examples are invalid")
        schema = _validated_object_schema(input_schema)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "capability_id", capability_id)
        object.__setattr__(self, "description", description.strip())
        object.__setattr__(
            self, "examples", tuple(item.strip() for item in example_values)
        )
        object.__setattr__(self, "risk", risk)
        object.__setattr__(self, "approval_required", approval_required)
        object.__setattr__(
            self,
            "_schema_json",
            json.dumps(
                schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
        )

    @property
    def input_schema(self) -> dict[str, object]:
        """Return a defensive JSON copy suitable for a model request."""
        return json.loads(self._schema_json)

    def validate_arguments(self, value: object) -> dict[str, IntentValue]:
        if not isinstance(value, Mapping):
            raise ValueError(f"arguments for {self.operation} must be an object")
        schema = self.input_schema
        properties = schema["properties"]
        required = set(schema["required"])
        unknown = set(value) - set(properties)
        missing = required - set(value)
        if unknown:
            raise ValueError(
                f"unsupported arguments for {self.operation}: {sorted(unknown)}"
            )
        if missing:
            raise ValueError(
                f"missing arguments for {self.operation}: {sorted(missing)}"
            )
        return {
            key: _validated_value(raw, properties[key], key)
            for key, raw in value.items()
        }


class OperationCatalog:
    def __init__(self, definitions: Iterable[OperationDefinition]) -> None:
        values = tuple(definitions)
        if not values or len(values) > 128:
            raise ValueError("operation catalog must contain from 1 to 128 entries")
        if any(not isinstance(item, OperationDefinition) for item in values):
            raise TypeError("operation catalog entries are invalid")
        operations = [item.operation for item in values]
        capabilities = [item.capability_id for item in values]
        if len(set(operations)) != len(operations):
            raise ValueError("operation catalog contains duplicate operations")
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("operation catalog contains duplicate capabilities")
        self._values = values
        self._by_operation = {item.operation: item for item in values}

    def definitions(self) -> tuple[OperationDefinition, ...]:
        return self._values

    def operation(self, name: str) -> OperationDefinition:
        try:
            return self._by_operation[name]
        except KeyError as error:
            raise ValueError(f"operation is not available: {name}") from error

    def select(
        self, operations: Iterable[str] | None
    ) -> tuple[OperationDefinition, ...]:
        if operations is None:
            return self._values
        names = tuple(dict.fromkeys(operations))
        if not names:
            raise ValueError("selected operations cannot be empty")
        return tuple(self.operation(name) for name in names)


def build_operation_definitions(
    capability_contracts: Iterable[object],
    *,
    available_capabilities: Iterable[str],
    policy_source: Callable[[str], object],
) -> tuple[OperationDefinition, ...]:
    """Join untrusted descriptions with executable capabilities and trusted policy."""
    available = set(available_capabilities)
    result: list[OperationDefinition] = []
    for contract in capability_contracts:
        capability_id = getattr(contract, "capability_id", None)
        route = getattr(contract, "user_intent", None)
        if capability_id not in available or route is None:
            continue
        policy = policy_source(capability_id)
        risk_value = getattr(getattr(policy, "risk", None), "value", None)
        try:
            risk = RiskClass(risk_value)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"trusted policy risk is invalid for {capability_id}"
            ) from error
        schema = getattr(contract, "input_schema", None)
        normalized_schema = _validated_object_schema(schema)
        allowed_arguments = getattr(policy, "allowed_arguments", None)
        required_arguments = getattr(policy, "required_arguments", None)
        if not isinstance(allowed_arguments, frozenset) or not isinstance(
            required_arguments, frozenset
        ):
            raise ValueError(
                f"trusted policy arguments are invalid for {capability_id}"
            )
        properties = set(normalized_schema["properties"])
        required = set(normalized_schema["required"])
        if not properties <= allowed_arguments or not required_arguments <= required:
            raise ValueError(
                f"module input schema conflicts with trusted policy for {capability_id}"
            )
        result.append(
            OperationDefinition(
                getattr(route, "operation", None),
                capability_id,
                getattr(route, "description", None),
                normalized_schema,
                examples=getattr(route, "examples", ()),
                risk=risk,
                approval_required=getattr(policy, "plan_approval_required", None),
            )
        )
    return OperationCatalog(result).definitions()


def _validated_object_schema(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {
        "type",
        "properties",
        "required",
        "additionalProperties",
    }:
        raise ValueError("operation input schema has invalid fields")
    if value.get("type") != "object" or value.get("additionalProperties") is not False:
        raise ValueError("operation input schema must be a closed object")
    properties = value.get("properties")
    required = value.get("required")
    if not isinstance(properties, Mapping) or len(properties) > 64:
        raise ValueError("operation input properties are invalid")
    if any(
        not isinstance(key, str) or not _PROPERTY.fullmatch(key) for key in properties
    ):
        raise ValueError("operation input property name is invalid")
    normalized = {
        key: _validated_property_schema(schema, array_item=False)
        for key, schema in properties.items()
    }
    if (
        not isinstance(required, list)
        or len(required) != len(set(required))
        or any(not isinstance(item, str) for item in required)
        or not set(required) <= set(normalized)
    ):
        raise ValueError("operation required arguments are invalid")
    return {
        "type": "object",
        "properties": normalized,
        "required": list(required),
        "additionalProperties": False,
    }


def _validated_property_schema(value: object, *, array_item: bool) -> dict[str, object]:
    if not isinstance(value, Mapping) or not value or set(value) - _SCHEMA_KEYS:
        raise ValueError("operation property schema is unsupported")
    kind = value.get("type")
    if kind not in _SCALAR_TYPES | {"array"} or array_item and kind == "array":
        raise ValueError("operation property type is unsupported")
    if kind == "array":
        if "items" not in value:
            raise ValueError("array operation property requires items")
        items = _validated_property_schema(value["items"], array_item=True)
        if items.get("type") != "string":
            raise ValueError("only string arrays are supported")
    elif "items" in value:
        raise ValueError("items is valid only for arrays")
    else:
        items = None
    description = value.get("description")
    if description is not None and (
        not isinstance(description, str) or not description or len(description) > 500
    ):
        raise ValueError("operation property description is invalid")
    enum = value.get("enum")
    if enum is not None and (
        not isinstance(enum, list)
        or not 1 <= len(enum) <= 64
        or len({json.dumps(item, sort_keys=True) for item in enum}) != len(enum)
    ):
        raise ValueError("operation property enum is invalid")
    result = dict(value)
    if items is not None:
        result["items"] = items
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    return result


def _validated_value(
    value: object, schema: Mapping[str, object], label: str
) -> IntentValue:
    kind = schema["type"]
    if kind == "string":
        if not isinstance(value, str):
            raise ValueError(f"{label} must be a string")
        result: IntentValue = value.strip()
        minimum = int(schema.get("minLength", 1))
        maximum = min(int(schema.get("maxLength", 2_000)), 4_000)
        if not minimum <= len(result) <= maximum or any(
            ord(character) < 32 for character in result
        ):
            raise ValueError(f"{label} has invalid length or control characters")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{label} must be a boolean")
        result = value
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{label} must be an integer")
        result = value
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{label} must be a number")
        result = value
    elif kind == "array":
        maximum = min(int(schema.get("maxItems", 100)), 100)
        minimum = int(schema.get("minItems", 0))
        if not isinstance(value, list) or not minimum <= len(value) <= maximum:
            raise ValueError(f"{label} must be a bounded array")
        result = tuple(_validated_value(item, schema["items"], label) for item in value)
    else:
        raise ValueError(f"{label} uses an unsupported type")
    enum = schema.get("enum")
    if enum is not None and result not in enum:
        raise ValueError(f"{label} is outside its enum")
    minimum_value = schema.get("minimum")
    maximum_value = schema.get("maximum")
    if isinstance(result, (int, float)) and not isinstance(result, bool):
        if minimum_value is not None and result < minimum_value:
            raise ValueError(f"{label} is below its minimum")
        if maximum_value is not None and result > maximum_value:
            raise ValueError(f"{label} is above its maximum")
    return result
