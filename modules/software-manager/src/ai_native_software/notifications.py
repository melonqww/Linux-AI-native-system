from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence


class NotificationDeliveryError(RuntimeError):
    """The desktop notification service did not accept a notification."""


class LinuxDesktopNotifier:
    """Deliver notifications through the freedesktop desktop notification service."""

    def __init__(
        self,
        command_path: str | None = None,
        *,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    ) -> None:
        self._command_path = command_path
        self._runner = runner

    def notify(self, title: str, body: str) -> None:
        command = self._command_path or shutil.which("notify-send")
        if command is None:
            raise NotificationDeliveryError("desktop_notification_client_unavailable")
        argv: Sequence[str] = (
            command,
            "--app-name=AI-native Linux",
            "--icon=system-software-install-symbolic",
            "--category=transfer.complete",
            "--",
            title,
            body,
        )
        try:
            result = self._runner(
                argv,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise NotificationDeliveryError("desktop_notification_delivery_failed") from error
        if result.returncode != 0:
            raise NotificationDeliveryError("desktop_notification_rejected")
