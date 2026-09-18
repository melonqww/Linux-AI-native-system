"""Trusted scan-scope exclusions for Security Center private state."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path, PurePosixPath


def normalize_exclusions(
    roots: Mapping[str, Path],
    excluded_paths: Mapping[str, Collection[str]] | None,
) -> dict[str, tuple[str, ...]]:
    if excluded_paths is None:
        return {resource_id: () for resource_id in roots}
    if not isinstance(excluded_paths, Mapping) or set(excluded_paths) - set(roots):
        raise ValueError("invalid_scan_exclusions")
    normalized: dict[str, tuple[str, ...]] = {}
    for resource_id in roots:
        values = excluded_paths.get(resource_id, ())
        if not isinstance(values, (list, tuple)) or len(values) > 32:
            raise ValueError("invalid_scan_exclusions")
        items: list[str] = []
        for value in values:
            if type(value) is not str or not 1 <= len(value) <= 1024:
                raise ValueError("invalid_scan_exclusions")
            candidate = PurePosixPath(value)
            if candidate.is_absolute() or any(
                part in {"", ".", ".."} for part in candidate.parts
            ):
                raise ValueError("invalid_scan_exclusions")
            items.append(candidate.as_posix())
        normalized[resource_id] = tuple(sorted(set(items)))
    return normalized


def is_excluded(
    exclusions: Mapping[str, tuple[str, ...]], resource_id: str, relative_path: str
) -> bool:
    return any(
        relative_path == excluded or relative_path.startswith(f"{excluded}/")
        for excluded in exclusions.get(resource_id, ())
    )


def storage_is_excluded(
    path: Path,
    roots: Mapping[str, str | Path],
    exclusions: Mapping[str, tuple[str, ...]],
) -> bool:
    absolute = path.expanduser().absolute()
    for resource_id, raw_root in roots.items():
        root = Path(raw_root).expanduser().resolve(strict=True)
        try:
            relative = absolute.relative_to(root).as_posix()
        except ValueError:
            continue
        if relative in {"", "."} or not is_excluded(
            exclusions, resource_id, relative
        ):
            return False
    return True
