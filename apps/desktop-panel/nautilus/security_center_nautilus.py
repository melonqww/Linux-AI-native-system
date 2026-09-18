"""Official Nautilus MenuProvider integration for Security Center."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Nautilus", "4.0")
from gi.repository import Gio, GLib, GObject, Nautilus


class SecurityCenterMenuProvider(GObject.GObject, Nautilus.MenuProvider):
    def get_file_items(self, files):
        if len(files) != 1 or files[0].get_uri_scheme() != "file":
            return []
        selected = files[0]
        target = "folder" if selected.is_directory() else "file"
        item = Nautilus.MenuItem(
            name="SecurityCenter::Scan",
            label="Проверить с помощью Security Center",
            tip="Запустить локальную проверку выбранного объекта",
        )
        item.connect("activate", self._scan, selected.get_uri(), target)
        return [item]

    @staticmethod
    def _scan(_item, uri, target):
        helper = Path(__file__).with_name("security_center_scan.py")
        python = GLib.find_program_in_path("python3")
        if python is None:
            return
        try:
            Gio.Subprocess.new(
                [python, str(helper), target, uri],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
        except Exception:
            return
