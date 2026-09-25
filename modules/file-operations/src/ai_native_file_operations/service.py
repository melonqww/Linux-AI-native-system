"""Isolated, bounded filesystem implementation for File Operations R1."""

from __future__ import annotations

import errno
import os
import stat
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from ai_native_orchestrator import CapabilityExecutionError


SelectionResolver = Callable[[str], Sequence[str | os.PathLike[str]]]
_DESTINATION_ROLES = frozenset({"desktop", "documents", "downloads"})
_CONFLICT_POLICIES = frozenset({"fail", "rename"})


class FileOperationError(CapabilityExecutionError):
    """A stable, user-safe failure at the file-operation boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)


class OperationKind(StrEnum):
    CREATE_DIRECTORY = "create_directory"
    MOVE_RESULTS = "move_results"
    RENAME_ITEM = "rename_item"
    TRASH_RESULTS = "trash_results"


class PlanState(StrEnum):
    PREPARED = "prepared"
    COMMITTING = "committing"
    COMMITTED = "committed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class _Snapshot:
    path: Path
    root_role: str
    relative_path: str
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int


@dataclass(slots=True)
class _Plan:
    plan_id: str
    operation: OperationKind
    created_at: str
    expires_at: float
    sources: tuple[_Snapshot, ...] = ()
    destination: str | None = None
    destination_path: Path | None = None
    directory_name: str | None = None
    new_name: str | None = None
    conflict_policy: str = "fail"
    state: PlanState = PlanState.PREPARED
    error_code: str | None = None


class FileOperationsService:
    """Prepare and commit bounded operations over trusted selections.

    The service never accepts a raw path from a model-facing argument. Existing
    objects are resolved through ``selection_resolver``; destination roots are
    supplied by trusted host configuration. Plans are process-local and become
    invalid after restart, which fails closed until durable plan storage lands.
    """

    def __init__(
        self,
        *,
        destination_roots: Mapping[str, str | os.PathLike[str]],
        trash_root: str | os.PathLike[str],
        selection_resolver: SelectionResolver,
        plan_ttl_seconds: int = 300,
        max_items: int = 100,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if set(destination_roots) != _DESTINATION_ROLES:
            raise ValueError("destination_roots_must_define_desktop_documents_downloads")
        if not callable(selection_resolver):
            raise TypeError("selection_resolver_must_be_callable")
        if not isinstance(plan_ttl_seconds, int) or not 1 <= plan_ttl_seconds <= 900:
            raise ValueError("invalid_plan_ttl")
        if not isinstance(max_items, int) or not 1 <= max_items <= 1000:
            raise ValueError("invalid_max_items")
        roots = {
            role: self._existing_safe_directory(path, "unsafe_destination_root")
            for role, path in destination_roots.items()
        }
        root_values = tuple(roots.values())
        if any(
            left != right
            and (_same_or_descendant(left, right) or _same_or_descendant(right, left))
            for index, left in enumerate(root_values)
            for right in root_values[index + 1 :]
        ):
            raise ValueError("destination_roots_overlap")
        requested_trash = Path(os.path.abspath(os.fspath(trash_root)))
        trash_parent = self._existing_safe_directory(
            requested_trash.parent, "unsafe_trash_parent"
        )
        trash = trash_parent / requested_trash.name
        if trash.exists() or trash.is_symlink():
            trash = self._existing_safe_directory(trash, "unsafe_trash_directory")
        if any(_same_or_descendant(trash, root) for root in roots.values()):
            raise ValueError("trash_root_overlaps_destination")
        self._roots = roots
        self._trash_root = trash
        self._resolver = selection_resolver
        self._ttl = plan_ttl_seconds
        self._max_items = max_items
        self._clock = clock
        self._plans: dict[str, _Plan] = {}
        self._lock = threading.RLock()

    @property
    def plan_ttl_seconds(self) -> int:
        return self._ttl

    def inspect(self, results_from: object) -> dict[str, object]:
        snapshots = []
        unavailable_count = 0
        for value in self._selection_values(results_from):
            try:
                snapshots.append(self._snapshot(value))
            except FileOperationError as error:
                if error.code != "selection_outside_allowed_roots":
                    raise
                # Search can cover volumes beyond the file-operations roots.
                # Do not inspect those entries or fail metadata for the
                # permitted members of the same trusted selection.
                unavailable_count += 1
        if not snapshots:
            raise FileOperationError("selection_outside_allowed_roots")
        if len({item.path for item in snapshots}) != len(snapshots):
            raise FileOperationError("duplicate_selection_item")
        return {
            "state": "completed",
            "item_count": len(snapshots),
            "unavailable_count": unavailable_count,
            "items": [
                {
                    "name": snapshot.path.name,
                    "kind": "directory"
                    if stat.S_ISDIR(snapshot.mode)
                    else "file",
                    "size_bytes": snapshot.size,
                    "modified_ns": snapshot.modified_ns,
                    "location": snapshot.root_role,
                    "relative_path": snapshot.relative_path,
                }
                for snapshot in snapshots
            ],
        }

    def prepare_create_directory(
        self,
        destination: object,
        directory_name: object,
        *,
        trusted_destination: object | None = None,
    ) -> dict[str, object]:
        role, destination_path = self._destination(destination, trusted_destination)
        name = _safe_name(directory_name)
        return self._store_plan(
            _Plan(
                plan_id=str(uuid4()),
                operation=OperationKind.CREATE_DIRECTORY,
                created_at=_utc_now(),
                expires_at=self._clock() + self._ttl,
                destination=role,
                destination_path=destination_path,
                directory_name=name,
            )
        )

    def prepare_move(
        self,
        results_from: object,
        destination: object,
        *,
        directory_name: object | None = None,
        conflict_policy: object = "fail",
        trusted_destination: object | None = None,
    ) -> dict[str, object]:
        sources = self._selection(results_from)
        role, destination_path = self._destination(destination, trusted_destination)
        name = None if directory_name is None else _safe_name(directory_name)
        policy = _conflict_policy(conflict_policy)
        return self._store_plan(
            _Plan(
                plan_id=str(uuid4()),
                operation=OperationKind.MOVE_RESULTS,
                created_at=_utc_now(),
                expires_at=self._clock() + self._ttl,
                sources=sources,
                destination=role,
                destination_path=destination_path,
                directory_name=name,
                conflict_policy=policy,
            )
        )

    def prepare_rename(
        self,
        results_from: object,
        new_name: object,
        *,
        conflict_policy: object = "fail",
    ) -> dict[str, object]:
        sources = self._selection(results_from)
        if len(sources) != 1:
            raise FileOperationError("rename_requires_one_item")
        return self._store_plan(
            _Plan(
                plan_id=str(uuid4()),
                operation=OperationKind.RENAME_ITEM,
                created_at=_utc_now(),
                expires_at=self._clock() + self._ttl,
                sources=sources,
                new_name=_safe_name(new_name),
                conflict_policy=_conflict_policy(conflict_policy),
            )
        )

    def prepare_trash(self, results_from: object) -> dict[str, object]:
        return self._store_plan(
            _Plan(
                plan_id=str(uuid4()),
                operation=OperationKind.TRASH_RESULTS,
                created_at=_utc_now(),
                expires_at=self._clock() + self._ttl,
                sources=self._selection(results_from),
            )
        )

    def cancel(self, plan_id: object) -> dict[str, object]:
        with self._lock:
            plan = self._plan(plan_id)
            if plan.state is not PlanState.PREPARED:
                raise FileOperationError("plan_not_prepared")
            plan.state = PlanState.CANCELLED
            return self._public_plan(plan)

    def commit(self, plan_id: object) -> dict[str, object]:
        with self._lock:
            plan = self._plan(plan_id)
            if plan.state is not PlanState.PREPARED:
                raise FileOperationError("plan_not_prepared")
            if self._clock() > plan.expires_at:
                plan.state = PlanState.FAILED
                plan.error_code = "plan_expired"
                raise FileOperationError(plan.error_code)
            plan.state = PlanState.COMMITTING
            try:
                output = self._execute(plan)
            except FileOperationError as error:
                plan.state = PlanState.FAILED
                plan.error_code = error.code
                raise
            except OSError as error:
                plan.state = PlanState.FAILED
                plan.error_code = "filesystem_operation_failed"
                raise FileOperationError(plan.error_code) from error
            plan.state = PlanState.COMMITTED
            return {**self._public_plan(plan), **output}

    def plan(self, plan_id: object) -> dict[str, object]:
        with self._lock:
            return self._public_plan(self._plan(plan_id))

    def _execute(self, plan: _Plan) -> dict[str, object]:
        if plan.operation is OperationKind.CREATE_DIRECTORY:
            assert plan.destination is not None and plan.directory_name is not None
            root = self._safe_destination(plan)
            target = root / plan.directory_name
            _ensure_absent(target)
            target.mkdir(mode=0o700)
            return {"created_count": 1, "destination": plan.destination}
        self._verify_snapshots(plan.sources)
        if plan.operation is OperationKind.MOVE_RESULTS:
            return self._execute_move(plan)
        if plan.operation is OperationKind.RENAME_ITEM:
            return self._execute_rename(plan)
        if plan.operation is OperationKind.TRASH_RESULTS:
            return self._execute_trash(plan)
        raise FileOperationError("unsupported_operation")

    def _execute_move(self, plan: _Plan) -> dict[str, object]:
        assert plan.destination is not None
        root = self._safe_destination(plan)
        container = root
        created_container = False
        if plan.directory_name is not None:
            container = root / plan.directory_name
            if container.exists() or container.is_symlink():
                _require_safe_directory(container, root)
            else:
                container.mkdir(mode=0o700)
                created_container = True
        targets = _targets_for_sources(
            plan.sources, container, plan.conflict_policy
        )
        for source, target in zip(plan.sources, targets):
            if _same_or_descendant(target, source.path):
                if created_container:
                    container.rmdir()
                raise FileOperationError("destination_inside_source")
        moved: list[tuple[Path, Path]] = []
        try:
            for source, target in zip(plan.sources, targets):
                _atomic_move(source.path, target)
                moved.append((source.path, target))
        except (FileOperationError, OSError) as error:
            _rollback_moves(moved)
            if created_container:
                _remove_empty(container)
            if isinstance(error, FileOperationError):
                raise
            raise FileOperationError("move_failed") from error
        return {
            "moved_count": len(moved),
            "destination": plan.destination,
            "directory_name": plan.directory_name,
        }

    def _execute_rename(self, plan: _Plan) -> dict[str, object]:
        assert len(plan.sources) == 1 and plan.new_name is not None
        source = plan.sources[0].path
        target = _available_target(source.parent / plan.new_name, plan.conflict_policy)
        if target == source:
            raise FileOperationError("rename_is_noop")
        _atomic_move(source, target)
        return {"renamed_count": 1, "new_name": target.name}

    def _execute_trash(self, plan: _Plan) -> dict[str, object]:
        files = self._trash_root / "files"
        info = self._trash_root / "info"
        _ensure_private_directory(self._trash_root)
        _ensure_private_directory(files)
        _ensure_private_directory(info)
        targets = _targets_for_sources(plan.sources, files, "rename")
        moved: list[tuple[Path, Path]] = []
        info_paths: list[Path] = []
        try:
            for source, target in zip(plan.sources, targets):
                _atomic_move(source.path, target)
                moved.append((source.path, target))
                info_path = info / f"{target.name}.trashinfo"
                _write_trash_info(info_path, source.path)
                info_paths.append(info_path)
        except (FileOperationError, OSError) as error:
            for path in info_paths:
                path.unlink(missing_ok=True)
            _rollback_moves(moved)
            if isinstance(error, FileOperationError):
                raise
            raise FileOperationError("trash_failed") from error
        return {"trashed_count": len(moved)}

    def _selection(self, results_from: object) -> tuple[_Snapshot, ...]:
        snapshots = tuple(self._snapshot(value) for value in self._selection_values(results_from))
        paths = [item.path for item in snapshots]
        if len(set(paths)) != len(paths):
            raise FileOperationError("duplicate_selection_item")
        return snapshots

    def _selection_values(self, results_from: object) -> Sequence[str | os.PathLike[str]]:
        if not isinstance(results_from, str) or not results_from.strip():
            raise FileOperationError("invalid_results_reference")
        try:
            resolved = self._resolver(results_from)
        except Exception as error:
            raise FileOperationError("selection_unavailable") from error
        if isinstance(resolved, (str, bytes)) or not isinstance(resolved, Sequence):
            raise FileOperationError("invalid_selection")
        if not 1 <= len(resolved) <= self._max_items:
            raise FileOperationError("selection_size_out_of_bounds")
        return resolved

    def _snapshot(self, value: str | os.PathLike[str]) -> _Snapshot:
        try:
            path = Path(os.path.abspath(os.fspath(value)))
        except (TypeError, ValueError, OSError) as error:
            raise FileOperationError("invalid_selection_path") from error
        role, root = self._containing_root(path)
        _reject_reparse_chain(root, path)
        try:
            metadata = path.stat(follow_symlinks=False)
        except OSError as error:
            raise FileOperationError("selection_item_unavailable") from error
        if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
            raise FileOperationError("unsupported_item_type")
        return _Snapshot(
            path=path,
            root_role=role,
            relative_path=path.relative_to(root).as_posix(),
            device=metadata.st_dev,
            inode=metadata.st_ino,
            mode=metadata.st_mode,
            size=metadata.st_size,
            modified_ns=metadata.st_mtime_ns,
        )

    def _verify_snapshots(self, snapshots: tuple[_Snapshot, ...]) -> None:
        for snapshot in snapshots:
            role, root = self._containing_root(snapshot.path)
            if role != snapshot.root_role:
                raise FileOperationError("selection_changed")
            _reject_reparse_chain(root, snapshot.path)
            try:
                current = snapshot.path.stat(follow_symlinks=False)
            except OSError as error:
                raise FileOperationError("selection_changed") from error
            identity = (
                current.st_dev,
                current.st_ino,
                current.st_mode,
                current.st_size,
                current.st_mtime_ns,
            )
            expected = (
                snapshot.device,
                snapshot.inode,
                snapshot.mode,
                snapshot.size,
                snapshot.modified_ns,
            )
            if identity != expected:
                raise FileOperationError("selection_changed")

    def _containing_root(self, path: Path) -> tuple[str, Path]:
        matches = [
            (role, root)
            for role, root in self._roots.items()
            if _same_or_descendant(path, root)
        ]
        if len(matches) != 1:
            raise FileOperationError("selection_outside_allowed_roots")
        return matches[0]

    def _destination(
        self, value: object, trusted_destination: object | None
    ) -> tuple[str, Path | None]:
        if isinstance(value, str) and value in self._roots:
            self._safe_root(value)
            return value, None
        if (
            isinstance(value, str)
            and isinstance(trusted_destination, str)
            and value == trusted_destination
        ):
            path = Path(os.path.abspath(value))
            _role, root = self._containing_root(path)
            _reject_reparse_chain(root, path)
            _require_safe_directory(path, root)
            return "context.last_destination", path
        raise FileOperationError("invalid_destination")

    def _safe_destination(self, plan: _Plan) -> Path:
        if plan.destination_path is None:
            assert plan.destination is not None
            return self._safe_root(plan.destination)
        _role, root = self._containing_root(plan.destination_path)
        _reject_reparse_chain(root, plan.destination_path)
        _require_safe_directory(plan.destination_path, root)
        return plan.destination_path

    def _safe_root(self, role: str) -> Path:
        root = self._roots[role]
        return self._existing_safe_directory(root, "unsafe_destination_root")

    @staticmethod
    def _existing_safe_directory(value: str | os.PathLike[str], code: str) -> Path:
        path = Path(os.path.abspath(os.fspath(value)))
        if not path.exists() or not path.is_dir() or _is_reparse_point(path):
            raise ValueError(code)
        return path.resolve(strict=True)

    def _store_plan(self, plan: _Plan) -> dict[str, object]:
        with self._lock:
            self._plans[plan.plan_id] = plan
            return self._public_plan(plan)

    def _plan(self, value: object) -> _Plan:
        if not isinstance(value, str):
            raise FileOperationError("invalid_plan_id")
        plan = self._plans.get(value)
        if plan is None:
            raise FileOperationError("plan_not_found")
        return plan

    @staticmethod
    def _public_plan(plan: _Plan) -> dict[str, object]:
        is_create = plan.operation is OperationKind.CREATE_DIRECTORY
        return {
            "plan_id": plan.plan_id,
            "operation": plan.operation.value,
            "state": plan.state.value,
            "created_at": plan.created_at,
            "item_count": 1 if is_create else len(plan.sources),
            "item_names": (
                (plan.directory_name,)
                if is_create and plan.directory_name is not None
                else tuple(snapshot.path.name for snapshot in plan.sources)
            ),
            "total_bytes": sum(snapshot.size for snapshot in plan.sources),
            "destination": plan.destination,
            "directory_name": plan.directory_name,
            "new_name": plan.new_name,
            "conflict_policy": plan.conflict_policy,
            "error_code": plan.error_code,
        }


def _safe_name(value: object) -> str:
    if not isinstance(value, str):
        raise FileOperationError("invalid_name")
    if (
        not value
        or value != value.strip()
        or value in {".", ".."}
        or len(value.encode("utf-8")) > 255
        or any(character in value for character in ("/", "\\", "\0"))
        or any(ord(character) < 32 for character in value)
    ):
        raise FileOperationError("invalid_name")
    return value


def _conflict_policy(value: object) -> str:
    if not isinstance(value, str) or value not in _CONFLICT_POLICIES:
        raise FileOperationError("invalid_conflict_policy")
    return value


def _same_or_descendant(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _reject_reparse_chain(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise FileOperationError("selection_outside_allowed_roots") from error
    current = root
    if _is_reparse_point(current):
        raise FileOperationError("unsafe_path_component")
    for part in relative.parts:
        current /= part
        if _is_reparse_point(current):
            raise FileOperationError("unsafe_path_component")


def _require_safe_directory(path: Path, root: Path) -> None:
    _reject_reparse_chain(root, path)
    if not path.is_dir():
        raise FileOperationError("destination_is_not_directory")


def _ensure_absent(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise FileOperationError("destination_exists")


def _available_target(path: Path, conflict_policy: str) -> Path:
    if not path.exists() and not path.is_symlink():
        return path
    if conflict_policy == "fail":
        raise FileOperationError("destination_exists")
    stem = path.stem if path.suffix else path.name
    suffix = path.suffix
    for ordinal in range(1, 1001):
        candidate = path.with_name(f"{stem} ({ordinal}){suffix}")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    raise FileOperationError("unique_name_unavailable")


def _targets_for_sources(
    sources: tuple[_Snapshot, ...], destination: Path, conflict_policy: str
) -> tuple[Path, ...]:
    reserved: set[Path] = set()
    targets: list[Path] = []
    for source in sources:
        candidate = destination / source.path.name
        target = _available_target(candidate, conflict_policy)
        while target in reserved:
            if conflict_policy == "fail":
                raise FileOperationError("duplicate_destination_name")
            target = _available_target(
                target.with_name(f"{target.stem} (1){target.suffix}"), "rename"
            )
        reserved.add(target)
        targets.append(target)
    return tuple(targets)


def _atomic_move(source: Path, target: Path) -> None:
    _ensure_absent(target)
    try:
        os.replace(source, target)
    except OSError as error:
        if error.errno == errno.EXDEV:
            raise FileOperationError("cross_device_move_unsupported") from error
        raise


def _rollback_moves(moved: list[tuple[Path, Path]]) -> None:
    failures = False
    for original, target in reversed(moved):
        try:
            if original.exists() or original.is_symlink():
                failures = True
                continue
            os.replace(target, original)
        except OSError:
            failures = True
    if failures:
        raise FileOperationError("rollback_failed")


def _ensure_private_directory(path: Path) -> None:
    if path.exists() or path.is_symlink():
        if _is_reparse_point(path) or not path.is_dir():
            raise FileOperationError("unsafe_trash_directory")
        return
    path.mkdir(mode=0o700)


def _write_trash_info(path: Path, original: Path) -> None:
    content = (
        "[Trash Info]\n"
        f"Path={quote(str(original), safe='/')}\n"
        f"DeletionDate={datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%dT%H:%M:%S')}\n"
    )
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    except FileExistsError as error:
        raise FileOperationError("trash_metadata_exists") from error


def _remove_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        return


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
