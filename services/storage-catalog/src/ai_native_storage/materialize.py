"""Approval-bound, rollback-aware copying of snapshot collection files."""

from __future__ import annotations

import os
import errno
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import RLock
from time import monotonic
from uuid import uuid4

from .collections import VirtualCollectionStore


class MaterializeError(RuntimeError):
    """Base class for safe module-specific failure classification."""


class MaterializeIntegrityError(MaterializeError):
    pass


class MaterializeDestinationError(MaterializeError):
    pass


class MaterializeSpaceError(OSError):
    pass


class MaterializeRollbackError(MaterializeError):
    pass


@dataclass(frozen=True)
class MaterializeItem:
    source: str
    destination_name: str
    size_bytes: int
    mtime_ns: int
    device: int
    inode: int
    sha256: str


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
    def __init__(self, *, ttl_seconds: float = 300) -> None:
        if ttl_seconds <= 0:
            raise ValueError("approval TTL must be positive")
        self._ttl_seconds = ttl_seconds
        self._tokens: dict[str, tuple[str, float]] = {}
        self._lock = RLock()

    def approve(self, plan_id: str, *, user_confirmed: bool) -> ApprovalGrant:
        if not user_confirmed:
            raise PermissionError("explicit user confirmation is required")
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge()
            self._tokens[plan_id] = (token, monotonic() + self._ttl_seconds)
        return ApprovalGrant(plan_id=plan_id, token=token)

    def consume(self, grant: ApprovalGrant) -> None:
        with self._lock:
            self._purge()
            stored = self._tokens.pop(grant.plan_id, None)
        if stored is None or not secrets.compare_digest(stored[0], grant.token):
            raise PermissionError("approval grant is invalid or already consumed")

    def revoke(self, plan_id: str) -> None:
        with self._lock:
            self._tokens.pop(plan_id, None)

    def _purge(self) -> None:
        now = monotonic()
        for plan_id in [key for key, value in self._tokens.items() if value[1] <= now]:
            self._tokens.pop(plan_id, None)


class MaterializeService:
    def __init__(
        self,
        database_path: Path,
        approval: ApprovalAuthority,
        *,
        plan_ttl_seconds: float = 600,
    ) -> None:
        if plan_ttl_seconds <= 0:
            raise ValueError("materialize plan TTL must be positive")
        self.collections = VirtualCollectionStore(database_path)
        self.approval = approval
        self._plan_ttl_seconds = plan_ttl_seconds
        self._plans: dict[str, tuple[MaterializePlan, float]] = {}
        self._lock = RLock()

    def create_copy_plan(
        self,
        collection_id: str,
        destination: Path,
        *,
        deadline_monotonic: float | None = None,
    ) -> MaterializePlan:
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
            self._check_deadline(deadline_monotonic)
            source = Path(reference.path)
            if not reference.available or source.is_symlink() or not source.is_file():
                continue
            name = self._unique_name(reference.name, used_names, destination)
            stat = source.stat()
            items.append(
                MaterializeItem(
                    str(source),
                    name,
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_dev,
                    stat.st_ino,
                    self._digest(source, deadline_monotonic),
                )
            )
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
        with self._lock:
            self._purge_plans()
            self._plans[plan.plan_id] = (plan, monotonic() + self._plan_ttl_seconds)
        return plan

    def execute(
        self,
        plan_id: str,
        grant: ApprovalGrant,
        *,
        deadline_monotonic: float | None = None,
    ) -> tuple[str, ...]:
        with self._lock:
            self._purge_plans()
            stored = self._plans.pop(plan_id, None)
        if stored is None or grant.plan_id != plan_id:
            raise KeyError("unknown materialize plan")
        plan = stored[0]
        self.approval.consume(grant)
        destination = Path(plan.destination)
        if destination.exists() and destination.is_symlink():
            raise MaterializeDestinationError("destination changed after approval")
        created_directory = not destination.exists()
        destination.mkdir(parents=False, exist_ok=True)
        if destination.is_symlink() or not destination.is_dir():
            raise MaterializeDestinationError("destination is not a normal directory")
        destination_identity = destination.stat()
        if shutil.disk_usage(destination).free < plan.total_bytes:
            if created_directory:
                destination.rmdir()
            raise MaterializeSpaceError("insufficient free space for copy plan")
        created: list[Path] = []
        try:
            for item in plan.items:
                self._check_deadline(deadline_monotonic)
                source = Path(item.source)
                stat = source.stat()
                if (
                    source.is_symlink()
                    or stat.st_size != item.size_bytes
                    or stat.st_mtime_ns != item.mtime_ns
                    or stat.st_dev != item.device
                    or stat.st_ino != item.inode
                    or self._digest(source, deadline_monotonic) != item.sha256
                ):
                    raise MaterializeIntegrityError("source changed after approval")
                target = destination / item.destination_name
                if target.exists():
                    raise FileExistsError(target)
                current_destination = destination.stat()
                if (
                    destination.is_symlink()
                    or current_destination.st_dev != destination_identity.st_dev
                    or current_destination.st_ino != destination_identity.st_ino
                ):
                    raise MaterializeDestinationError("destination changed during copy")
                handle, temporary_name = tempfile.mkstemp(prefix=".ai-copy-", dir=destination)
                os.close(handle)
                temporary = Path(temporary_name)
                try:
                    self._copy_with_deadline(source, temporary, deadline_monotonic)
                    if self._digest(temporary, deadline_monotonic) != item.sha256:
                        raise MaterializeIntegrityError(
                            "source changed while it was being copied"
                        )
                    self._publish_no_clobber(
                        temporary, target, created, deadline_monotonic
                    )
                finally:
                    temporary.unlink(missing_ok=True)
        except Exception as error:
            rollback_failed = False
            for path in reversed(created):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    rollback_failed = True
            if created_directory:
                try:
                    destination.rmdir()
                except OSError:
                    rollback_failed = True
            if rollback_failed:
                raise MaterializeRollbackError("copy rollback was incomplete") from error
            raise
        return tuple(str(path) for path in created)

    def discard(self, plan_id: str) -> None:
        with self._lock:
            self._plans.pop(plan_id, None)
        self.approval.revoke(plan_id)

    def _purge_plans(self) -> None:
        now = monotonic()
        expired = [key for key, value in self._plans.items() if value[1] <= now]
        for plan_id in expired:
            self._plans.pop(plan_id, None)
            self.approval.revoke(plan_id)

    @staticmethod
    def _digest(path: Path, deadline_monotonic: float | None = None) -> str:
        digest = sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                MaterializeService._check_deadline(deadline_monotonic)
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _copy_with_deadline(
        source: Path, destination: Path, deadline_monotonic: float | None
    ) -> None:
        with source.open("rb") as input_stream, destination.open("wb") as output_stream:
            while chunk := input_stream.read(1024 * 1024):
                MaterializeService._check_deadline(deadline_monotonic)
                output_stream.write(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        shutil.copystat(source, destination, follow_symlinks=False)

    @staticmethod
    def _check_deadline(deadline_monotonic: float | None) -> None:
        if deadline_monotonic is not None and monotonic() >= deadline_monotonic:
            raise TimeoutError("materialize operation deadline expired")

    @staticmethod
    def _publish_no_clobber(
        temporary: Path,
        target: Path,
        created: list[Path],
        deadline_monotonic: float | None,
    ) -> None:
        try:
            # Hard-link publication is atomic and cannot replace an existing
            # target. The temporary file is on the destination filesystem.
            os.link(temporary, target, follow_symlinks=False)
            created.append(target)
            return
        except FileExistsError:
            raise
        except OSError as error:
            unsupported = {
                errno.EPERM,
                errno.EACCES,
                getattr(errno, "EOPNOTSUPP", errno.EPERM),
                getattr(errno, "ENOTSUP", errno.EPERM),
                getattr(errno, "ENOSYS", errno.EPERM),
            }
            if error.errno not in unsupported:
                raise

        # FAT/exFAT and some network filesystems do not support hard links.
        # O_EXCL still guarantees no user file is overwritten; rollback removes
        # the visible partial target if streaming fails.
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created.append(target)
        try:
            with temporary.open("rb") as source, os.fdopen(descriptor, "wb") as output:
                while chunk := source.read(1024 * 1024):
                    MaterializeService._check_deadline(deadline_monotonic)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            shutil.copystat(temporary, target, follow_symlinks=False)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    @staticmethod
    def _unique_name(name: str, used: set[str], destination: Path) -> str:
        candidate = name
        stem, suffix = Path(name).stem, Path(name).suffix
        counter = 2
        while candidate.casefold() in used or (destination / candidate).exists():
            candidate = f"{stem} ({counter}){suffix}"
            counter += 1
        return candidate
