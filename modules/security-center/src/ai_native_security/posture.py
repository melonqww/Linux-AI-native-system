"""Bounded, read-only Ubuntu security posture collection."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys


POSTURE_SCHEMA_VERSION = 1
_MAX_COMMAND_OUTPUT = 1024 * 1024
_MAX_PROC_LINES = 4096
_MAX_AUTOSTART_ENTRIES = 256
_PACKAGE = re.compile(r"^\s*Inst\s+")


@dataclass(frozen=True, slots=True)
class PostureObservation:
    check_id: str
    state: str
    severity: str
    evidence_code: str
    count: int | None = None

    def __post_init__(self) -> None:
        if self.check_id not in {
            "security_updates",
            "firewall",
            "apparmor",
            "listening_ports",
            "autostart",
            "scanner_rules",
        }:
            raise ValueError("invalid_posture_check")
        if self.state not in {"healthy", "finding", "unknown"}:
            raise ValueError("invalid_posture_state")
        if self.severity not in {"info", "low", "medium", "high"}:
            raise ValueError("invalid_posture_severity")
        if not _bounded_code(self.evidence_code):
            raise ValueError("invalid_posture_evidence")
        if self.count is not None and (type(self.count) is not int or self.count < 0):
            raise ValueError("invalid_posture_count")

    def to_dict(self) -> dict[str, object]:
        return {
            "check_id": self.check_id,
            "state": self.state,
            "severity": self.severity,
            "evidence_code": self.evidence_code,
            "count": self.count,
        }


class UbuntuPostureCollector:
    """Collect six fixed observations without changing system state."""

    def __init__(
        self,
        *,
        platform: str | None = None,
        run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        apparmor_enabled: Path = Path("/sys/module/apparmor/parameters/enabled"),
        proc_net: Path = Path("/proc/net"),
        system_autostart: Path = Path("/etc/xdg/autostart"),
        user_autostart: Path | None = None,
        apt_get: Path | None = None,
        ufw: Path | None = None,
        signature_version: str = "unknown",
        clamd_configured: bool = False,
    ) -> None:
        self.platform = platform or sys.platform
        self._run = run_fn or subprocess.run
        self._apparmor_enabled = apparmor_enabled
        self._proc_net = proc_net
        self._autostart = tuple(
            path
            for path in (system_autostart, user_autostart)
            if path is not None
        )
        self._apt_get = apt_get if apt_get is not None else _first_executable(
            (Path("/usr/bin/apt-get"), Path("/bin/apt-get"))
        )
        self._ufw = ufw if ufw is not None else _first_executable(
            (Path("/usr/sbin/ufw"), Path("/sbin/ufw"))
        )
        self._signature_version = signature_version
        self._clamd_configured = clamd_configured

    def scan(self) -> dict[str, object]:
        if not self.platform.startswith("linux"):
            return {
                "schema_version": POSTURE_SCHEMA_VERSION,
                "supported": False,
                "status": "unsupported",
                "verdict": "unknown",
                "observations": [],
            }
        observations = (
            self._security_updates(),
            self._firewall(),
            self._apparmor(),
            self._listening_ports(),
            self._autostarts(),
            self._scanner_rules(),
        )
        has_finding = any(item.state == "finding" for item in observations)
        has_unknown = any(item.state == "unknown" for item in observations)
        return {
            "schema_version": POSTURE_SCHEMA_VERSION,
            "supported": True,
            "status": "partial" if has_unknown else "completed",
            "verdict": (
                "findings_detected"
                if has_finding
                else "unknown"
                if has_unknown
                else "no_findings"
            ),
            "observations": [item.to_dict() for item in observations],
        }

    def _security_updates(self) -> PostureObservation:
        if self._apt_get is None:
            return _unknown("security_updates", "apt_unavailable")
        result = self._command(
            [
                str(self._apt_get),
                "--simulate",
                "--quiet=2",
                "--no-list-cleanup",
                "-o",
                "Debug::NoLocking=1",
                "dist-upgrade",
            ],
            timeout=20,
        )
        if result is None:
            return _unknown("security_updates", "update_check_failed")
        security_count = sum(
            1
            for line in result.splitlines()
            if _PACKAGE.match(line) and "-security" in line.casefold()
        )
        return PostureObservation(
            "security_updates",
            "finding" if security_count else "healthy",
            "high" if security_count else "info",
            "security_updates_available" if security_count else "security_updates_clear",
            security_count,
        )

    def _firewall(self) -> PostureObservation:
        if self._ufw is None:
            return _unknown("firewall", "firewall_tool_unavailable")
        result = self._command([str(self._ufw), "status"], timeout=3)
        if result is None:
            return _unknown("firewall", "firewall_check_failed")
        first_line = result.splitlines()[0].strip().casefold() if result.splitlines() else ""
        if first_line == "status: active":
            return PostureObservation("firewall", "healthy", "info", "firewall_active")
        if first_line == "status: inactive":
            return PostureObservation("firewall", "finding", "medium", "firewall_inactive")
        return _unknown("firewall", "firewall_status_unknown")

    def _apparmor(self) -> PostureObservation:
        try:
            with self._apparmor_enabled.open("r", encoding="ascii") as stream:
                value = stream.read(16).strip()
        except (OSError, UnicodeError):
            return _unknown("apparmor", "apparmor_status_unavailable")
        enabled = value.casefold() in {"y", "yes", "1"}
        return PostureObservation(
            "apparmor",
            "healthy" if enabled else "finding",
            "info" if enabled else "high",
            "apparmor_enabled" if enabled else "apparmor_disabled",
        )

    def _listening_ports(self) -> PostureObservation:
        count = 0
        available = False
        for name in ("tcp", "tcp6"):
            path = self._proc_net / name
            try:
                with path.open("r", encoding="ascii", errors="strict") as stream:
                    lines = []
                    for index, line in enumerate(stream):
                        if index > _MAX_PROC_LINES:
                            return _unknown(
                                "listening_ports", "port_inventory_limited"
                            )
                        lines.append(line)
            except (OSError, UnicodeError):
                continue
            available = True
            for line in lines[1 : _MAX_PROC_LINES + 1]:
                fields = line.split()
                if len(fields) > 3 and fields[3] == "0A":
                    count += 1
        if not available:
            return _unknown("listening_ports", "port_inventory_unavailable")
        return PostureObservation(
            "listening_ports",
            "healthy",
            "info",
            "listening_ports_inventory",
            count,
        )

    def _autostarts(self) -> PostureObservation:
        count = 0
        available = False
        for directory in self._autostart:
            try:
                with os.scandir(directory) as entries:
                    for index, entry in enumerate(entries):
                        if index >= _MAX_AUTOSTART_ENTRIES:
                            return _unknown("autostart", "autostart_inventory_limited")
                        try:
                            if (
                                entry.name.endswith(".desktop")
                                and entry.is_file(follow_symlinks=False)
                            ):
                                count += 1
                        except OSError:
                            return _unknown("autostart", "autostart_inventory_failed")
                available = True
            except OSError:
                continue
        if not available:
            return _unknown("autostart", "autostart_inventory_unavailable")
        return PostureObservation(
            "autostart", "healthy", "info", "autostart_inventory", count
        )

    def _scanner_rules(self) -> PostureObservation:
        if not _bounded_code(self._signature_version):
            return _unknown("scanner_rules", "scanner_rules_invalid")
        return PostureObservation(
            "scanner_rules",
            "healthy",
            "info",
            "clamd_and_local_rules" if self._clamd_configured else "local_rules_active",
            2 if self._clamd_configured else 1,
        )

    def _command(self, command: list[str], *, timeout: int) -> str | None:
        try:
            result = self._run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env={
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                    "LANG": "C",
                    "LC_ALL": "C",
                    "DEBIAN_FRONTEND": "noninteractive",
                },
                cwd="/",
            )
        except (OSError, subprocess.SubprocessError):
            return None
        stdout = result.stdout if isinstance(result.stdout, str) else ""
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        if result.returncode != 0 or len((stdout + stderr).encode("utf-8")) > _MAX_COMMAND_OUTPUT:
            return None
        return stdout


def _unknown(check_id: str, evidence: str) -> PostureObservation:
    return PostureObservation(check_id, "unknown", "info", evidence)


def _first_executable(paths: Iterable[Path]) -> Path | None:
    for path in paths:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def _bounded_code(value: object) -> bool:
    return type(value) is str and 1 <= len(value) <= 64 and value.isascii()
