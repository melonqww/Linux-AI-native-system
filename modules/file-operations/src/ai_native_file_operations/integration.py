"""Capability handler adapter for the core execution contracts."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from uuid import uuid4

from ai_native_orchestrator import (
    ApprovalRequest,
    ExecutionSnapshot,
    OperationOutput,
    PreparedOperation,
)
from ai_native_permissions import CapabilityInvocation, ExecutionPhase

from .service import FileOperationsService


CAPABILITY_IDS = (
    "files.items.inspect",
    "files.directory.create",
    "files.items.move",
    "files.items.rename",
    "files.items.trash",
)


class FileOperationsCapabilityHandler:
    """Translate generic invocations into the isolated file service."""

    def __init__(self, service: FileOperationsService) -> None:
        self._service = service

    def __call__(self, invocation: CapabilityInvocation) -> object:
        snapshot = invocation.trusted_payload
        if not isinstance(snapshot, ExecutionSnapshot) and invocation.phase is not ExecutionPhase.COMMIT:
            raise TypeError("file operation requires trusted execution state")
        if invocation.phase is ExecutionPhase.EXECUTE:
            if invocation.capability_id != "files.items.inspect":
                raise ValueError("file operation does not support execute")
            assert isinstance(snapshot, ExecutionSnapshot)
            collection_id = snapshot.resolve_selection(
                invocation.arguments.get("results_from")
            )
            result = self._service.inspect(collection_id)
            return OperationOutput(
                "inspect_files",
                int(result["item_count"]),
                {
                    "items": tuple(result["items"]),
                    "unavailable_count": int(result["unavailable_count"]),
                    **_inspection_messages(result),
                },
            )
        if invocation.phase is ExecutionPhase.PREPARE:
            assert isinstance(snapshot, ExecutionSnapshot)
            prepared = self._prepare(invocation, snapshot)
            plan_id = str(prepared["plan_id"])
            destination = str(
                prepared.get("destination")
                or prepared.get("new_name")
                or "trash"
            )
            request = ApprovalRequest(
                approval_request_id=str(uuid4()),
                plan_id=invocation.plan_id,
                step_id=invocation.step_id,
                action=str(prepared["operation"]),
                destination=destination,
                item_count=int(prepared["item_count"]),
                total_bytes=int(prepared["total_bytes"]),
                item_names=tuple(str(value) for value in prepared["item_names"]),
                expires_in_seconds=self._service.plan_ttl_seconds,
            )
            return PreparedOperation(request, plan_id, self._service.cancel)
        if invocation.phase is ExecutionPhase.COMMIT:
            result = self._service.commit(invocation.trusted_payload)
            return OperationOutput(
                str(result["operation"]),
                _affected_count(result),
                {**_safe_details(result), **_commit_messages(result)},
            )
        raise ValueError("unsupported file operation phase")

    def _prepare(
        self, invocation: CapabilityInvocation, snapshot: ExecutionSnapshot
    ) -> dict[str, object]:
        arguments = invocation.arguments
        capability = invocation.capability_id
        if capability == "files.directory.create":
            return self._service.prepare_create_directory(
                arguments.get("destination"),
                arguments.get("directory_name"),
                trusted_destination=snapshot.last_destination,
            )
        reference = snapshot.resolve_selection(arguments.get("results_from"))
        if capability == "files.items.move":
            return self._service.prepare_move(
                reference,
                arguments.get("destination"),
                directory_name=arguments.get("directory_name"),
                conflict_policy=arguments.get("conflict_policy", "fail"),
                trusted_destination=snapshot.last_destination,
            )
        if capability == "files.items.rename":
            return self._service.prepare_rename(
                reference,
                arguments.get("new_name"),
                conflict_policy=arguments.get("conflict_policy", "fail"),
            )
        if capability == "files.items.trash":
            return self._service.prepare_trash(reference)
        raise ValueError("unsupported file operation capability")


def capability_handlers(
    service: FileOperationsService,
) -> Mapping[str, Callable[[CapabilityInvocation], object]]:
    handler = FileOperationsCapabilityHandler(service)
    return {capability_id: handler for capability_id in CAPABILITY_IDS}


def _affected_count(result: dict[str, object]) -> int:
    for key in ("created_count", "moved_count", "renamed_count", "trashed_count"):
        value = result.get(key)
        if isinstance(value, int):
            return value
    return 0


def _safe_details(result: dict[str, object]) -> dict[str, object]:
    allowed = {
        "state",
        "destination",
        "directory_name",
        "new_name",
        "created_count",
        "moved_count",
        "renamed_count",
        "trashed_count",
    }
    return {key: value for key, value in result.items() if key in allowed}


def _inspection_messages(result: dict[str, object]) -> dict[str, str]:
    items = result["items"]
    assert isinstance(items, list)
    descriptions_ru = []
    descriptions_en = []
    for item in items[:5]:
        name = json.dumps(str(item["name"]), ensure_ascii=False)
        place = json.dumps(
            str(item["location"]) + "/" + str(item["relative_path"]),
            ensure_ascii=False,
        )
        size = int(item["size_bytes"])
        descriptions_ru.append(f"{name}: {size} байт, {place}")
        descriptions_en.append(f"{name}: {size} bytes, {place}")
    skipped = int(result["unavailable_count"])
    suffix_ru = f" Недоступно вне разрешённых папок: {skipped}." if skipped else ""
    suffix_en = f" Outside allowed folders: {skipped}." if skipped else ""
    return {
        "summary_ru": f"Проверено объектов: {len(items)}. {'; '.join(descriptions_ru)}.{suffix_ru}",
        "summary_en": f"Items inspected: {len(items)}. {'; '.join(descriptions_en)}.{suffix_en}",
    }


def _commit_messages(result: dict[str, object]) -> dict[str, str]:
    operation = result["operation"]
    count = _affected_count(result)
    labels = {
        "create_directory": ("Создано папок", "Folders created"),
        "move_results": ("Перемещено объектов", "Items moved"),
        "rename_item": ("Переименовано объектов", "Items renamed"),
        "trash_results": ("Отправлено в корзину объектов", "Items sent to trash"),
    }
    ru, en = labels[str(operation)]
    return {"summary_ru": f"{ru}: {count}.", "summary_en": f"{en}: {count}."}
