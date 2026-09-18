"""Deterministic, bounded quick and full scan profiles."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import stat
import time
from types import MappingProxyType
from typing import Callable, Mapping

from .contracts import ScanResult
from .scope import is_excluded, normalize_exclusions


CAMPAIGN_SCHEMA_VERSION = 1
MAX_RETURNED_FINDING_IDS = 64


@dataclass(frozen=True, slots=True)
class ScanProfile:
    max_files: int
    max_total_bytes: int
    max_depth: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if not 1 <= self.max_files <= 100_000:
            raise ValueError("invalid_profile_max_files")
        if not 1 <= self.max_total_bytes <= 64 * 1024 * 1024 * 1024:
            raise ValueError("invalid_profile_max_bytes")
        if not 0 <= self.max_depth <= 64:
            raise ValueError("invalid_profile_max_depth")
        if not 0.01 <= self.timeout_seconds <= 60:
            raise ValueError("invalid_profile_timeout")


DEFAULT_PROFILES = MappingProxyType(
    {
        "quick": ScanProfile(
            max_files=128,
            max_total_bytes=64 * 1024 * 1024,
            max_depth=3,
            timeout_seconds=15,
        ),
        "full": ScanProfile(
            max_files=100_000,
            max_total_bytes=64 * 1024 * 1024 * 1024,
            max_depth=64,
            timeout_seconds=60,
        ),
    }
)


class CampaignScanner:
    """Enumerate one trusted resource and delegate each file to FileScanner."""

    def __init__(
        self,
        allowed_roots: Mapping[str, str | os.PathLike[str]],
        scan_file: Callable[[str, str], ScanResult],
        record_findings: Callable[[ScanResult], tuple[int, ...]],
        *,
        profiles: Mapping[str, ScanProfile] = DEFAULT_PROFILES,
        excluded_paths: Mapping[str, Collection[str]] | None = None,
    ) -> None:
        self._roots = MappingProxyType(
            {
                resource_id: Path(root).resolve(strict=True)
                for resource_id, root in allowed_roots.items()
            }
        )
        self._scan_file = scan_file
        self._record_findings = record_findings
        self._exclusions = MappingProxyType(
            normalize_exclusions(self._roots, excluded_paths)
        )
        if set(profiles) != {"quick", "full"}:
            raise ValueError("invalid_scan_profiles")
        self._profiles = MappingProxyType(dict(profiles))

    def run(
        self,
        resource_id: object,
        mode: object,
        relative_path: object = "",
    ) -> dict[str, object]:
        if type(resource_id) is not str or resource_id not in self._roots:
            return self._rejected(
                resource_id, mode, relative_path, "resource_not_available"
            )
        if type(mode) is not str or mode not in self._profiles:
            return self._rejected(resource_id, mode, relative_path, "invalid_scan_mode")
        normalized = self._directory(self._roots[resource_id], relative_path)
        if normalized is None:
            return self._rejected(
                resource_id, mode, relative_path, "directory_not_available"
            )
        scan_root, prefix = normalized
        if prefix and is_excluded(self._exclusions, resource_id, prefix):
            return self._rejected(resource_id, mode, relative_path, "protected_path")
        profile = self._profiles[mode]
        started = time.monotonic()
        deadline = started + profile.timeout_seconds
        scanned = threats = unknown = skipped = total_bytes = 0
        limited = False
        finding_ids: list[int] = []

        for relative_path, size_bytes in self._candidates(
            scan_root,
            profile.max_depth,
            deadline,
            profile.max_files * 8,
            tuple(
                self._roots[resource_id].joinpath(*item.split("/"))
                for item in self._exclusions[resource_id]
            ),
        ):
            if relative_path is None:
                if size_bytes < 0:
                    limited = True
                    break
                unknown += 1
                skipped += 1
                continue
            if (
                time.monotonic() > deadline
                or scanned >= profile.max_files
                or total_bytes + size_bytes > profile.max_total_bytes
            ):
                limited = True
                break
            candidate = relative_path if not prefix else f"{prefix}/{relative_path}"
            result = self._scan_file(resource_id, candidate)
            scanned += 1
            if result.size_bytes is not None:
                total_bytes += result.size_bytes
            if result.verdict == "malware_detected":
                threats += 1
                ids = self._record_findings(result)
                remaining = MAX_RETURNED_FINDING_IDS - len(finding_ids)
                finding_ids.extend(ids[: max(0, remaining)])
            elif result.verdict == "unknown":
                unknown += 1
            if result.status == "rejected":
                skipped += 1

        status = "partial" if limited or unknown else "completed"
        verdict = (
            "malware_detected"
            if threats
            else "unknown"
            if status == "partial"
            else "no_threat_detected"
        )
        return {
            "schema_version": CAMPAIGN_SCHEMA_VERSION,
            "resource_id": resource_id,
            "relative_path": prefix,
            "mode": mode,
            "status": status,
            "verdict": verdict,
            "error_code": (
                "profile_limit_reached"
                if limited
                else "scan_incomplete"
                if unknown
                else None
            ),
            "scanned_files": scanned,
            "scanned_bytes": total_bytes,
            "threat_files": threats,
            "unknown_files": unknown,
            "skipped_files": skipped,
            "finding_ids": finding_ids,
        }

    @staticmethod
    def _candidates(
        root: Path,
        max_depth: int,
        deadline: float,
        max_entries: int,
        excluded_roots: tuple[Path, ...] = (),
    ):
        pending: list[tuple[Path, int]] = [(root, 0)]
        visited_entries = 0
        while pending:
            if time.monotonic() > deadline:
                yield None, -1
                return
            directory, depth = pending.pop()
            try:
                entries = []
                with os.scandir(directory) as iterator:
                    for entry in iterator:
                        visited_entries += 1
                        if (
                            visited_entries > max_entries
                            or time.monotonic() > deadline
                        ):
                            yield None, -1
                            return
                        entries.append(entry)
                entries.sort(key=lambda item: item.name)
            except OSError:
                yield None, 0
                continue
            directories: list[Path] = []
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    yield None, 0
                    continue
                if stat.S_ISLNK(info.st_mode):
                    yield None, 0
                    continue
                path = Path(entry.path)
                if any(
                    path == excluded or excluded in path.parents
                    for excluded in excluded_roots
                ):
                    continue
                if _is_junction(path):
                    yield None, 0
                    continue
                if stat.S_ISREG(info.st_mode):
                    yield path.relative_to(root).as_posix(), info.st_size
                elif stat.S_ISDIR(info.st_mode) and depth < max_depth:
                    directories.append(path)
            for child in reversed(directories):
                pending.append((child, depth + 1))

    @staticmethod
    def _directory(root: Path, relative_path: object) -> tuple[Path, str] | None:
        if type(relative_path) is not str or len(relative_path) > 1024:
            return None
        if relative_path in {"", "."}:
            return root, ""
        candidate = PurePosixPath(relative_path)
        if candidate.is_absolute() or any(
            part in {"", ".", ".."} for part in candidate.parts
        ):
            return None
        current = root
        try:
            for part in candidate.parts:
                current = current / part
                info = current.lstat()
                if stat.S_ISLNK(info.st_mode) or _is_junction(current):
                    return None
            if not current.is_dir():
                return None
            current.resolve(strict=True).relative_to(root)
        except (OSError, ValueError):
            return None
        return current, candidate.as_posix()

    @staticmethod
    def _rejected(
        resource_id: object,
        mode: object,
        relative_path: object,
        error: str,
    ) -> dict[str, object]:
        return {
            "schema_version": CAMPAIGN_SCHEMA_VERSION,
            "resource_id": resource_id if type(resource_id) is str else "",
            "mode": mode if type(mode) is str else "",
            "relative_path": relative_path if type(relative_path) is str else "",
            "status": "rejected",
            "verdict": "unknown",
            "error_code": error,
            "scanned_files": 0,
            "scanned_bytes": 0,
            "threat_files": 0,
            "unknown_files": 0,
            "skipped_files": 0,
            "finding_ids": [],
        }


def _is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    if checker is None:
        return False
    try:
        return bool(checker())
    except OSError:
        return True
