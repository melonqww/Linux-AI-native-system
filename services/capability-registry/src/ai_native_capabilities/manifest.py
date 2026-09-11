"""Strict, dependency-free validation of module.json files."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .contracts import (
    CapabilityContract,
    IntentRouteDescriptor,
    ModuleEntrypoint,
    ModuleManifest,
)


MANIFEST_FILENAME = "module.json"
MAX_MANIFEST_BYTES = 64 * 1024
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")
_PYTHON_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$")
_EXPECTED_FIELDS = frozenset(
    {
        "schema_version",
        "module_id",
        "display_name",
        "description",
        "module_version",
        "core_api",
        "capabilities",
        "dependencies",
        "optional_dependencies",
        "requested_permissions",
        "lifecycle",
        "resource_class",
        "default_enabled",
        "entrypoint",
    }
)
_ENTRYPOINT_FIELDS = frozenset({"kind", "module", "python_path"})
_CAPABILITY_FIELDS = frozenset(
    {"id", "description", "input_schema", "requested_permissions", "user_intent"}
)
_USER_INTENT_REQUIRED_FIELDS = frozenset({"operation", "description", "examples"})
_USER_INTENT_FIELDS = _USER_INTENT_REQUIRED_FIELDS | {"preserved_arguments"}
_INPUT_SCHEMA_FIELDS = frozenset(
    {"type", "properties", "required", "additionalProperties"}
)


class ManifestValidationError(ValueError):
    """Raised when a module manifest cannot be trusted by the Registry."""


def load_manifest(path: Path) -> ModuleManifest:
    path = path.expanduser().resolve(strict=True)
    if not path.is_file():
        raise ManifestValidationError("manifest path must be a file")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ManifestValidationError("manifest is larger than 64 KiB")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestValidationError(f"cannot parse manifest: {error}") from error
    manifest = validate_manifest(payload)

    module_root = path.parent
    python_path = (module_root / manifest.entrypoint.python_path).resolve(strict=False)
    try:
        python_path.relative_to(module_root)
    except ValueError as error:
        raise ManifestValidationError("entrypoint.python_path escapes the module directory") from error
    if not python_path.is_dir():
        raise ManifestValidationError("entrypoint.python_path does not exist")
    return manifest


def validate_manifest(payload: object) -> ModuleManifest:
    if not isinstance(payload, dict):
        raise ManifestValidationError("manifest must be a JSON object")
    if set(payload) != _EXPECTED_FIELDS:
        raise ManifestValidationError("manifest fields do not match schema version 2")
    if payload["schema_version"] != 2:
        raise ManifestValidationError("schema_version must be 2")

    module_id = _identifier(payload["module_id"], "module_id")
    display_name = _text(payload["display_name"], "display_name", 100)
    description = _text(payload["description"], "description", 500)
    module_version = _pattern(payload["module_version"], "module_version", _SEMVER)
    core_api = _pattern(payload["core_api"], "core_api", re.compile(r"^[1-9][0-9]*$"))
    dependencies = _identifier_list(payload["dependencies"], "dependencies")
    optional_dependencies = _identifier_list(
        payload["optional_dependencies"], "optional_dependencies"
    )
    requested_permissions = _identifier_list(
        payload["requested_permissions"], "requested_permissions"
    )
    capabilities_payload = payload["capabilities"]
    if not isinstance(capabilities_payload, list) or not capabilities_payload:
        raise ManifestValidationError("capabilities must be a non-empty JSON array")
    if len(capabilities_payload) > 128:
        raise ManifestValidationError("capabilities must contain at most 128 items")
    capabilities: list[CapabilityContract] = []
    capability_ids: set[str] = set()
    route_operations: set[str] = set()
    for ordinal, capability in enumerate(capabilities_payload):
        capabilities.append(
            _capability_contract(
                capability,
                ordinal=ordinal,
                module_permissions=requested_permissions,
                capability_ids=capability_ids,
                route_operations=route_operations,
            )
        )
    if module_id in dependencies or module_id in optional_dependencies:
        raise ManifestValidationError("a module cannot depend on itself")
    if set(dependencies) & set(optional_dependencies):
        raise ManifestValidationError("required and optional dependencies overlap")

    lifecycle = payload["lifecycle"]
    if lifecycle not in {"on-demand", "background"}:
        raise ManifestValidationError("lifecycle must be on-demand or background")
    resource_class = payload["resource_class"]
    if resource_class not in {"tiny", "light", "medium", "heavy"}:
        raise ManifestValidationError("resource_class is invalid")
    default_enabled = payload["default_enabled"]
    if not isinstance(default_enabled, bool):
        raise ManifestValidationError("default_enabled must be boolean")

    entrypoint_payload = payload["entrypoint"]
    if not isinstance(entrypoint_payload, dict) or set(entrypoint_payload) != _ENTRYPOINT_FIELDS:
        raise ManifestValidationError("entrypoint fields do not match the schema")
    kind = entrypoint_payload["kind"]
    if kind not in {"python-package", "python-service"}:
        raise ManifestValidationError("entrypoint.kind is invalid")
    python_module = _pattern(entrypoint_payload["module"], "entrypoint.module", _PYTHON_MODULE)
    python_path = _relative_path(entrypoint_payload["python_path"])
    return ModuleManifest(
        schema_version=2,
        module_id=module_id,
        display_name=display_name,
        description=description,
        module_version=module_version,
        core_api=core_api,
        capabilities=tuple(capabilities),
        dependencies=dependencies,
        optional_dependencies=optional_dependencies,
        requested_permissions=requested_permissions,
        lifecycle=str(lifecycle),
        resource_class=str(resource_class),
        default_enabled=default_enabled,
        entrypoint=ModuleEntrypoint(
            kind=str(kind),
            module=python_module,
            python_path=python_path,
        ),
    )


def manifest_to_dict(manifest: ModuleManifest) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "module_id": manifest.module_id,
        "display_name": manifest.display_name,
        "description": manifest.description,
        "module_version": manifest.module_version,
        "core_api": manifest.core_api,
        "capabilities": [
            {
                "id": capability.capability_id,
                "description": capability.description,
                "input_schema": capability.input_schema,
                "requested_permissions": list(capability.requested_permissions),
                **(
                    {
                        "user_intent": {
                            "operation": capability.user_intent.operation,
                            "description": capability.user_intent.description,
                            "examples": list(capability.user_intent.examples),
                            "preserved_arguments": list(
                                capability.user_intent.preserved_arguments
                            ),
                        }
                    }
                    if capability.user_intent is not None
                    else {}
                ),
            }
            for capability in manifest.capabilities
        ],
        "dependencies": list(manifest.dependencies),
        "optional_dependencies": list(manifest.optional_dependencies),
        "requested_permissions": list(manifest.requested_permissions),
        "lifecycle": manifest.lifecycle,
        "resource_class": manifest.resource_class,
        "default_enabled": manifest.default_enabled,
        "entrypoint": {
            "kind": manifest.entrypoint.kind,
            "module": manifest.entrypoint.module,
            "python_path": manifest.entrypoint.python_path,
        },
    }


def _capability_contract(
    value: object,
    *,
    ordinal: int,
    module_permissions: tuple[str, ...],
    capability_ids: set[str],
    route_operations: set[str],
) -> CapabilityContract:
    if not isinstance(value, dict):
        raise ManifestValidationError(f"capability {ordinal} must be an object")
    if not set(value) <= _CAPABILITY_FIELDS or not (
        _CAPABILITY_FIELDS - {"user_intent"}
    ) <= set(value):
        raise ManifestValidationError(f"capability {ordinal} fields are invalid")
    capability_id = _identifier(value["id"], f"capability {ordinal} id")
    if capability_id in capability_ids:
        raise ManifestValidationError("capability IDs must be unique per module")
    capability_ids.add(capability_id)
    description = _text(value["description"], "capability description", 500)
    permissions = _identifier_list(
        value["requested_permissions"], "capability requested_permissions"
    )
    undeclared = set(permissions) - set(module_permissions)
    if undeclared:
        raise ManifestValidationError(
            "capability requests permissions absent from module requested_permissions"
        )
    input_schema = _input_schema(value["input_schema"])
    user_intent_payload = value.get("user_intent")
    user_intent = None
    if user_intent_payload is not None:
        if (
            not isinstance(user_intent_payload, dict)
            or not _USER_INTENT_REQUIRED_FIELDS <= set(user_intent_payload)
            or set(user_intent_payload) - _USER_INTENT_FIELDS
        ):
            raise ManifestValidationError("user_intent fields are invalid")
        operation = _pattern(
            user_intent_payload["operation"],
            "user_intent operation",
            re.compile(r"^[a-z][a-z0-9_]{0,63}$"),
        )
        if operation in route_operations:
            raise ManifestValidationError("user_intent operations must be unique per module")
        route_operations.add(operation)
        examples_value = user_intent_payload["examples"]
        if not isinstance(examples_value, list) or not 1 <= len(examples_value) <= 32:
            raise ManifestValidationError("user_intent examples must contain from 1 to 32 items")
        preserved_value = user_intent_payload.get("preserved_arguments", [])
        properties = input_schema["properties"]
        if (
            not isinstance(preserved_value, list)
            or len(preserved_value) != len(set(preserved_value))
            or any(not isinstance(item, str) for item in preserved_value)
            or not set(preserved_value) <= set(properties)
        ):
            raise ManifestValidationError(
                "user_intent preserved_arguments must name input properties"
            )
        user_intent = IntentRouteDescriptor(
            capability_id=capability_id,
            operation=operation,
            description=_text(
                user_intent_payload["description"], "user_intent description", 500
            ),
            examples=tuple(
                _text(item, "user_intent example", 300) for item in examples_value
            ),
            preserved_arguments=tuple(preserved_value),
        )
    return CapabilityContract(
        capability_id=capability_id,
        description=description,
        input_schema=input_schema,
        requested_permissions=permissions,
        user_intent=user_intent,
    )


def _input_schema(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _INPUT_SCHEMA_FIELDS:
        raise ManifestValidationError("input_schema fields are invalid")
    if value.get("type") != "object" or value.get("additionalProperties") is not False:
        raise ManifestValidationError("input_schema must be a closed object schema")
    properties = value.get("properties")
    required = value.get("required")
    if not isinstance(properties, dict) or len(properties) > 64:
        raise ManifestValidationError("input_schema properties must be a bounded object")
    if any(
        not isinstance(name, str)
        or not re.fullmatch(r"^[a-z][a-z0-9_]{0,63}$", name)
        or not isinstance(schema, dict)
        or not schema
        for name, schema in properties.items()
    ):
        raise ManifestValidationError("input_schema contains an invalid property")
    if (
        not isinstance(required, list)
        or any(not isinstance(item, str) for item in required)
        or len(required) != len(set(required))
        or not set(required) <= set(properties)
    ):
        raise ManifestValidationError("input_schema required fields are invalid")
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as error:
        raise ManifestValidationError("input_schema must contain JSON values") from error
    if len(encoded.encode("utf-8")) > 16 * 1024:
        raise ManifestValidationError("input_schema is larger than 16 KiB")
    return json.loads(encoded)


def _identifier(value: object, label: str) -> str:
    return _pattern(value, label, _IDENTIFIER)


def _identifier_list(value: object, label: str, *, require_items: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or require_items and not value:
        raise ManifestValidationError(f"{label} must be a JSON array")
    if any(not isinstance(item, str) or not _IDENTIFIER.fullmatch(item) for item in value):
        raise ManifestValidationError(f"{label} contains an invalid identifier")
    if len(set(value)) != len(value):
        raise ManifestValidationError(f"{label} must not contain duplicates")
    return tuple(value)


def _text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ManifestValidationError(f"{label} must contain from 1 to {maximum} characters")
    return value.strip()


def _pattern(value: object, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ManifestValidationError(f"{label} has an invalid format")
    return value


def _relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise ManifestValidationError("entrypoint.python_path is invalid")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ManifestValidationError("entrypoint.python_path must stay inside the module")
    return value
