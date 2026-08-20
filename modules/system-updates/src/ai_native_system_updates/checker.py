"""Safe, bounded APT simulation; never refreshes or installs packages."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from .contracts import UpdateCheck


_PACKAGE = re.compile(r"^\s*Inst\s+([^\s:]+(?::[^\s]+)?)")
_MAX_OUTPUT_BYTES = 1024 * 1024
_MAX_PACKAGE_PREVIEW = 50
_MAX_LIST_ENTRIES = 512
_STALE_AFTER_SECONDS = 24 * 60 * 60
_APT_GET_PATHS = (Path("/usr/bin/apt-get"), Path("/bin/apt-get"))


class UbuntuUpdateChecker:
    def __init__(
        self,
        *,
        platform: str | None = None,
        apt_get: str | None = None,
        lists_root: Path = Path("/var/lib/apt/lists"),
        run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self.platform = platform or sys.platform
        self.apt_get = apt_get if apt_get is not None else self._find_apt_get()
        self.lists_root = lists_root
        self._run_fn = run_fn or subprocess.run
        self._now_fn = now_fn or time.time

    def check(self) -> UpdateCheck:
        if not self.platform.startswith("linux"):
            return self._unavailable(False, "platform_unsupported")
        if not self.apt_get:
            return self._unavailable(True, "apt_unavailable")

        cache_age = self._cache_age()
        cache_stale = cache_age is None or cache_age > _STALE_AFTER_SECONDS
        warnings = ["cache_stale"] if cache_stale else []
        environment = {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
            "DEBIAN_FRONTEND": "noninteractive",
        }
        try:
            result = self._run_fn(
                [
                    self.apt_get,
                    "--simulate",
                    "--quiet=2",
                    "--no-list-cleanup",
                    "-o",
                    "Debug::NoLocking=1",
                    "dist-upgrade",
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
                env=environment,
                cwd="/",
            )
        except (OSError, subprocess.SubprocessError):
            return self._unavailable(True, "check_failed", cache_age, cache_stale)
        stdout = result.stdout if isinstance(result.stdout, str) else ""
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        if len(stdout.encode("utf-8")) + len(stderr.encode("utf-8")) > _MAX_OUTPUT_BYTES:
            return self._unavailable(True, "output_too_large", cache_age, cache_stale)
        if result.returncode != 0:
            return self._unavailable(True, "check_failed", cache_age, cache_stale)

        names: list[str] = []
        security_count = 0
        available_count = 0
        for line in stdout.splitlines():
            match = _PACKAGE.match(line)
            if match is None:
                continue
            available_count += 1
            if "-security" in line.casefold():
                security_count += 1
            if len(names) < _MAX_PACKAGE_PREVIEW:
                names.append(match.group(1)[:120])
        state = "updates_available" if available_count else "up_to_date"
        return UpdateCheck(
            schema_version=1,
            supported=True,
            state=state,
            provider="apt",
            available_count=available_count,
            security_count=security_count,
            package_names=tuple(names),
            cache_age_seconds=cache_age,
            cache_stale=cache_stale,
            warnings=tuple(warnings),
        )

    def _cache_age(self) -> int | None:
        try:
            iterator = os.scandir(self.lists_root)
        except OSError:
            return None
        newest: float | None = None
        with iterator:
            for index, entry in enumerate(iterator):
                if index >= _MAX_LIST_ENTRIES:
                    break
                if entry.name in {"lock", "partial", "auxfiles"}:
                    continue
                try:
                    modified = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                newest = modified if newest is None else max(newest, modified)
        return None if newest is None else max(0, int(self._now_fn() - newest))

    @staticmethod
    def _unavailable(
        supported: bool,
        warning: str,
        cache_age: int | None = None,
        cache_stale: bool = True,
    ) -> UpdateCheck:
        return UpdateCheck(
            schema_version=1,
            supported=supported,
            state="unavailable",
            provider="apt",
            available_count=0,
            security_count=0,
            package_names=(),
            cache_age_seconds=cache_age,
            cache_stale=cache_stale,
            warnings=(warning,),
        )

    @staticmethod
    def _find_apt_get() -> str | None:
        for path in _APT_GET_PATHS:
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
        return None
