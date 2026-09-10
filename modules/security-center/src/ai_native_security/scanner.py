"""Bounded, read-only file scanner with trusted-root confinement."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import time
from types import MappingProxyType
from typing import Mapping

from .contracts import DetectorObservation, MAX_OBSERVATIONS, ScanResult


_RESOURCE_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SEVERITIES = frozenset({"low", "medium", "high", "critical"})
_EICAR_SHA256 = "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f"


@dataclass(frozen=True, slots=True)
class HashSignature:
    rule_id: str
    sha256: str
    classification: str
    severity: str = "high"


@dataclass(frozen=True, slots=True)
class ByteSignature:
    rule_id: str
    pattern: bytes
    classification: str
    severity: str = "high"


@dataclass(frozen=True, slots=True)
class SignatureDatabase:
    """Trusted, immutable signature snapshot used for one worker lifetime."""

    hashes: tuple[HashSignature, ...] = ()
    byte_patterns: tuple[ByteSignature, ...] = ()

    def __post_init__(self) -> None:
        if not self.hashes and not self.byte_patterns:
            raise ValueError("empty_signature_database")
        if len(self.hashes) + len(self.byte_patterns) > 4096:
            raise ValueError("too_many_signatures")
        rule_ids: set[str] = set()
        for item in (*self.hashes, *self.byte_patterns):
            if not _valid_label(item.rule_id) or not _valid_label(item.classification):
                raise ValueError("invalid_signature_label")
            if item.rule_id in rule_ids:
                raise ValueError("duplicate_signature_rule")
            if item.severity not in _SEVERITIES:
                raise ValueError("invalid_signature_severity")
            rule_ids.add(item.rule_id)
        for item in self.hashes:
            if not isinstance(item.sha256, str) or _SHA256.fullmatch(item.sha256) is None:
                raise ValueError("invalid_signature_hash")
        for item in self.byte_patterns:
            if (
                not isinstance(item.pattern, bytes)
                or not item.pattern
                or len(item.pattern) > 256
            ):
                raise ValueError("invalid_byte_signature")

    @classmethod
    def builtin(cls) -> "SignatureDatabase":
        # EICAR is an industry-standard harmless anti-malware test file.
        return cls(
            hashes=(
                HashSignature(
                    rule_id="eicar-test-file",
                    sha256=_EICAR_SHA256,
                    classification="anti-malware-test",
                ),
            )
        )


class FileScanner:
    """Scan regular files beneath roots provisioned by trusted bootstrap code."""

    def __init__(
        self,
        allowed_roots: Mapping[str, str | os.PathLike[str]],
        *,
        signatures: SignatureDatabase | None = None,
        max_file_bytes: int = 8 * 1024 * 1024,
        chunk_bytes: int = 64 * 1024,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not 1 <= max_file_bytes <= 128 * 1024 * 1024:
            raise ValueError("invalid_max_file_bytes")
        if not 1024 <= chunk_bytes <= 1024 * 1024:
            raise ValueError("invalid_chunk_bytes")
        if not 0.01 <= timeout_seconds <= 60:
            raise ValueError("invalid_timeout")
        roots: dict[str, Path] = {}
        for resource_id, raw_root in allowed_roots.items():
            if _RESOURCE_ID.fullmatch(resource_id) is None:
                raise ValueError("invalid_resource_id")
            root = Path(raw_root)
            if root.is_symlink() or _is_junction(root):
                raise ValueError("unsafe_resource_root")
            resolved = root.resolve(strict=True)
            if not resolved.is_dir():
                raise ValueError("invalid_resource_root")
            roots[resource_id] = resolved
        self._roots = MappingProxyType(roots)
        self._signatures = signatures or SignatureDatabase.builtin()
        self._max_file_bytes = max_file_bytes
        self._chunk_bytes = chunk_bytes
        self._timeout_seconds = timeout_seconds

    def scan(self, resource_id: object, relative_path: object) -> ScanResult:
        normalized = _normalize_relative_path(relative_path)
        if not isinstance(resource_id, str) or _RESOURCE_ID.fullmatch(resource_id) is None:
            return _rejected("invalid_resource_id", "", normalized)
        root = self._roots.get(resource_id)
        if root is None:
            return _rejected("resource_not_available", resource_id, normalized)
        if not normalized:
            return _rejected("invalid_relative_path", resource_id, "")

        lexical = root.joinpath(*normalized.split("/"))
        try:
            _reject_link_components(root, lexical)
            resolved = lexical.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return _rejected("resource_not_accessible", resource_id, normalized)
        if not resolved.is_file():
            return _rejected("not_regular_file", resource_id, normalized)

        return self._scan_open_file(resource_id, normalized, root, resolved)

    def _scan_open_file(
        self, resource_id: str, relative_path: str, root: Path, path: Path
    ) -> ScanResult:
        descriptor: int | None = None
        try:
            descriptor = _open_confined(root, relative_path, path)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                return _rejected("not_regular_file", resource_id, relative_path)
            if before.st_size > self._max_file_bytes:
                return _rejected("file_too_large", resource_id, relative_path)

            digest = hashlib.sha256()
            matched_patterns: set[str] = set()
            overlap = b""
            total = 0
            deadline = time.monotonic() + self._timeout_seconds
            max_pattern = max(
                (len(item.pattern) for item in self._signatures.byte_patterns),
                default=1,
            )
            while True:
                if time.monotonic() > deadline:
                    return _failed("scan_timeout", resource_id, relative_path)
                block = os.read(descriptor, min(self._chunk_bytes, self._max_file_bytes - total + 1))
                if not block:
                    break
                total += len(block)
                if total > self._max_file_bytes:
                    return _rejected("file_too_large", resource_id, relative_path)
                digest.update(block)
                window = overlap + block
                for signature in self._signatures.byte_patterns:
                    if signature.rule_id not in matched_patterns and signature.pattern in window:
                        matched_patterns.add(signature.rule_id)
                overlap = window[-(max_pattern - 1) :] if max_pattern > 1 else b""

            after = os.fstat(descriptor)
            if _identity(before) != _identity(after) or total != after.st_size:
                return _failed("file_changed_during_scan", resource_id, relative_path)
            digest_hex = digest.hexdigest()
            observations = self._observations(digest_hex, matched_patterns)
            return ScanResult(
                resource_id=resource_id,
                relative_path=relative_path,
                status="completed",
                verdict="malware_detected" if observations else "no_threat_detected",
                sha256=digest_hex,
                size_bytes=total,
                observations=observations,
            )
        except OSError:
            return _failed("scan_io_error", resource_id, relative_path)
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _observations(
        self, digest: str, matched_patterns: set[str]
    ) -> tuple[DetectorObservation, ...]:
        found: list[DetectorObservation] = []
        for item in self._signatures.hashes:
            if item.sha256 == digest:
                found.append(_observation("sha256-signature", item))
        for item in self._signatures.byte_patterns:
            if item.rule_id in matched_patterns:
                found.append(_observation("byte-signature", item))
        found.sort(key=lambda item: (item.detector, item.rule_id))
        return tuple(found[:MAX_OBSERVATIONS])


def _observation(detector: str, item: HashSignature | ByteSignature) -> DetectorObservation:
    return DetectorObservation(
        detector=detector,
        rule_id=item.rule_id,
        classification=item.classification,
        severity=item.severity,
    )


def _normalize_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > 1024:
        return ""
    if "\\" in value:
        return ""
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return ""
    if value.startswith("/") or ":" in parts[0]:
        return ""
    return "/".join(parts)


def _reject_link_components(root: Path, candidate: Path) -> None:
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        if current.is_symlink() or _is_junction(current):
            raise ValueError("link_not_allowed")


def _open_confined(root: Path, relative_path: str, resolved: Path) -> int:
    """Open through directory descriptors on Linux; use checked fallback elsewhere."""

    read_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if os.open in os.supports_dir_fd and nofollow and directory:
        parent_fd = os.open(root, read_flags | directory | nofollow)
        try:
            parts = relative_path.split("/")
            for part in parts[:-1]:
                next_fd = os.open(
                    part,
                    read_flags | directory | nofollow,
                    dir_fd=parent_fd,
                )
                os.close(parent_fd)
                parent_fd = next_fd
            return os.open(parts[-1], read_flags | nofollow, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)

    descriptor = os.open(resolved, read_flags | nofollow)
    opened = os.fstat(descriptor)
    try:
        named = os.stat(resolved, follow_symlinks=False)
    except OSError:
        os.close(descriptor)
        raise
    if not stat.S_ISREG(named.st_mode) or (opened.st_dev, opened.st_ino) != (
        named.st_dev,
        named.st_ino,
    ):
        os.close(descriptor)
        raise OSError("resource_identity_changed")
    return descriptor


def _is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    return bool(checker()) if checker is not None else False


def _identity(item: os.stat_result) -> tuple[int, int, int, int]:
    return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)


def _valid_label(value: str) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 48 and value.isascii()


def _rejected(error: str, resource_id: str, relative_path: str) -> ScanResult:
    return ScanResult(
        resource_id=resource_id,
        relative_path=relative_path,
        status="rejected",
        verdict="unknown",
        error_code=error,
    )


def _failed(error: str, resource_id: str, relative_path: str) -> ScanResult:
    return ScanResult(
        resource_id=resource_id,
        relative_path=relative_path,
        status="failed",
        verdict="unknown",
        error_code=error,
    )


__all__ = [
    "ByteSignature",
    "FileScanner",
    "HashSignature",
    "SignatureDatabase",
]
