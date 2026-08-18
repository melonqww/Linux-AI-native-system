"""Strict, dependency-free validation of module.json files."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .contracts import ModuleEntrypoint, ModuleManifest


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
        raise ManifestValidationError("manifest fields do not match schema version 1")
    if payload["schema_version"] != 1:
        raise ManifestValidationError("schema_version must be 1")

    module_id = _identifier(payload["module_id"], "module_id")
    display_name = _text(payload["display_name"], "display_name", 100)
    description = _text(payload["description"], "description", 500)
    module_version = _pattern(payload["module_version"], "module_version", _SEMVER)
    core_api = _pattern(payload["core_api"], "core_api", re.compile(r"^[1-9][0-9]*$"))
    capabilities = _identifier_list(payload["capabilities"], "capabilities", require_items=True)
    dependencies = _identifier_list(payload["dependencies"], "dependencies")
    optional_dependencies = _identifier_list(
        payload["optional_dependencies"], "optional_dependencies"
    )
    requested_permissions = _identifier_list(
        payload["requested_permissions"], "requested_permissions"
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
        schema_version=1,
        module_id=module_id,
        display_name=display_name,
        description=description,
        module_version=module_version,
        core_api=core_api,
        capabilities=capabilities,
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
        "capabilities": list(manifest.capabilities),
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
