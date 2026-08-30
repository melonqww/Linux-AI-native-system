from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence


class NetworkManagerMonitor:
    """Read NetworkManager connectivity without generating network traffic."""

    def __init__(
        self,
        command_path: str | None = None,
        *,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    ) -> None:
        self._command_path = command_path
        self._runner = runner

    def is_online(self) -> bool | None:
        command = self._command_path or shutil.which("nm-online")
        if command is None:
            return None
        argv: Sequence[str] = (command, "--exit", "--quiet", "--timeout=1")
        try:
            result = self._runner(
                argv,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        return None
