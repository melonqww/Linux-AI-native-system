"""Approval-bound, rollback-aware copying of snapshot collection files."""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .collections import VirtualCollectionStore


@dataclass(frozen=True)
class MaterializeItem:
    source: str
    destination_name: str
    size_bytes: int
    mtime_ns: int


@dataclass(frozen=True)
class MaterializePlan:
    plan_id: str
    collection_id: str
    destination: str
    items: tuple[MaterializeItem, ...]
    total_bytes: int
    approval_required: bool = True


@dataclass(frozen=True)
class ApprovalGrant:
    plan_id: str
    token: str


class ApprovalAuthority:
    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}

    def approve(self, plan_id: str, *, user_confirmed: bool) -> ApprovalGrant:
        if not user_confirmed:
            raise PermissionError("explicit user confirmation is required")
        token = secrets.token_urlsafe(32)
        self._tokens[plan_id] = token
        return ApprovalGrant(plan_id=plan_id, token=token)

    def consume(self, grant: ApprovalGrant) -> None:
        expected = self._tokens.pop(grant.plan_id, None)
        if expected is None or not secrets.compare_digest(expected, grant.token):
            raise PermissionError("approval grant is invalid or already consumed")


class MaterializeService:
    def __init__(self, database_path: Path, approval: ApprovalAuthority) -> None:
        self.collections = VirtualCollectionStore(database_path)
        self.approval = approval
        self._plans: dict[str, MaterializePlan] = {}

    def create_copy_plan(self, collection_id: str, destination: Path) -> MaterializePlan:
        destination = destination.expanduser().absolute()
        if destination.name in {"", ".", ".."}:
            raise ValueError("destination must name a child directory")
        if destination.exists() and (destination.is_symlink() or not destination.is_dir()):
            raise ValueError("destination must be a normal directory")
        if not destination.parent.is_dir():
            raise ValueError("destination parent does not exist")
        destination = destination.parent.resolve(strict=True) / destination.name
        items: list[MaterializeItem] = []
        used_names: set[str] = set()
        for reference in self.collections.resolve(collection_id):
            source = Path(reference.path)
            if not reference.available or source.is_symlink() or not source.is_file():
                continue
            name = self._unique_name(reference.name, used_names, destination)
            stat = source.stat()
            items.append(MaterializeItem(str(source), name, stat.st_size, stat.st_mtime_ns))
            used_names.add(name.casefold())
        if not items:
            raise ValueError("collection has no available regular files")
        plan = MaterializePlan(
            plan_id=str(uuid4()),
            collection_id=collection_id,
            destination=str(destination),
            items=tuple(items),
            total_bytes=sum(item.size_bytes for item in items),
        )
        self._plans[plan.plan_id] = plan
        return plan

    def execute(self, plan_id: str, grant: ApprovalGrant) -> tuple[str, ...]:
        plan = self._plans.pop(plan_id, None)
        if plan is None or grant.plan_id != plan_id:
            raise KeyError("unknown materialize plan")
        self.approval.consume(grant)
        destination = Path(plan.destination)
        if destination.exists() and destination.is_symlink():
            raise RuntimeError("destination changed to a symbolic link after approval")
        created_directory = not destination.exists()
        destination.mkdir(parents=False, exist_ok=True)
        created: list[Path] = []
        try:
            for item in plan.items:
                source = Path(item.source)
                stat = source.stat()
                if source.is_symlink() or stat.st_size != item.size_bytes or stat.st_mtime_ns != item.mtime_ns:
                    raise RuntimeError(f"source changed after approval: {source}")
                target = destination / item.destination_name
                if target.exists():
                    raise FileExistsError(target)
                handle, temporary_name = tempfile.mkstemp(prefix=".ai-copy-", dir=destination)
                os.close(handle)
                temporary = Path(temporary_name)
                try:
                    shutil.copy2(source, temporary)
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
                created.append(target)
        except Exception:
            for path in reversed(created):
                path.unlink(missing_ok=True)
            if created_directory:
                destination.rmdir()
            raise
        return tuple(str(path) for path in created)

    @staticmethod
    def _unique_name(name: str, used: set[str], destination: Path) -> str:
        candidate = name
        stem, suffix = Path(name).stem, Path(name).suffix
        counter = 2
        while candidate.casefold() in used or (destination / candidate).exists():
            candidate = f"{stem} ({counter}){suffix}"
            counter += 1
        return candidate
