#!/usr/bin/env python3
"""Detached Security Center client used by the optional Nautilus extension."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname
from uuid import uuid4


MAX_MESSAGE_BYTES = 64 * 1024


def resolve_target(uri: str, home: Path | None = None) -> tuple[str, str]:
    """Convert one local file URI to the runtime's confined home-relative form."""

    parsed = urlsplit(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ValueError("unsupported_uri")
    root = (home or Path.home()).expanduser().resolve(strict=True)
    selected = Path(url2pathname(unquote(parsed.path))).resolve(strict=True)
    try:
        relative = selected.relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError("target_outside_home") from error
    if not relative or relative == ".":
        raise ValueError("home_requires_full_scan")
    return ("folder" if selected.is_dir() else "file", relative)


def request_scan(target: str, relative_path: str) -> dict[str, object]:
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    socket_path = Path(runtime) / "ai-native-linux" / "runtime.sock"
    body: dict[str, object] = {"target": target, "relative_path": relative_path}
    if target == "folder":
        body["mode"] = "full"
    request_id = str(uuid4())
    frame = json.dumps(
        {
            "version": 1,
            "request_id": request_id,
            "method": "POST",
            "path": "/v1/security/scan",
            "body": body,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    if len(frame) > MAX_MESSAGE_BYTES:
        raise RuntimeError("request_too_large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(130)
        client.connect(str(socket_path))
        client.sendall(frame)
        response = bytearray()
        while not response.endswith(b"\n"):
            chunk = client.recv(4096)
            if not chunk:
                raise RuntimeError("response_missing")
            response.extend(chunk)
            if len(response) > MAX_MESSAGE_BYTES:
                raise RuntimeError("response_too_large")
    envelope = json.loads(response)
    if (
        not isinstance(envelope, dict)
        or envelope.get("version") != 1
        or envelope.get("request_id") != request_id
    ):
        raise RuntimeError("response_invalid")
    if not 200 <= int(envelope.get("status", 0)) < 300:
        raise RuntimeError("scan_rejected")
    result = envelope.get("body")
    if not isinstance(result, dict):
        raise RuntimeError("response_invalid")
    return result


def result_message(result: dict[str, object]) -> str:
    scanned = int(result.get("scanned_files", 0))
    threats = int(result.get("threat_files", 0))
    skipped = int(result.get("skipped_files", 0))
    if threats:
        return f"Обнаружено угроз: {threats}. Проверено: {scanned}, пропущено: {skipped}."
    if result.get("status") == "completed" and result.get("verdict") == "no_threat_detected":
        return f"Угроз не обнаружено. Проверено файлов: {scanned}, пропущено: {skipped}."
    return f"Проверка завершена частично. Проверено: {scanned}, пропущено: {skipped}."


def notify(body: str) -> None:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    app = Gio.Application(
        application_id="io.github.melonqww.AINativeLinux.SecurityCenter",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    app.register(None)
    notification = Gio.Notification.new("Security Center")
    notification.set_body(body)
    app.send_notification(None, notification)
    GLib.usleep(200_000)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return 2
    expected_target, uri = argv
    try:
        target, relative_path = resolve_target(uri)
        if target != expected_target or target not in {"file", "folder"}:
            raise ValueError("target_type_changed")
        notify(result_message(request_scan(target, relative_path)))
        return 0
    except Exception:
        try:
            notify("Не удалось выполнить проверку выбранного объекта.")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
