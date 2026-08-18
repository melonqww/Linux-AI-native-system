#!/usr/bin/env python3
"""Minimal GTK/WebKit shell for the desktop panel prototype."""

import os
import sys
from pathlib import Path

# GTK3 cannot move a normal Wayland window. XWayland keeps this prototype
# positionable until the GNOME Shell integration is implemented.
if os.environ.get("XDG_SESSION_TYPE") == "wayland":
    os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")

from gi.repository import GLib, Gtk, WebKit2


DESKTOP_PANEL_ROOT = Path(__file__).resolve().parents[1]
PROTOTYPE_INDEX = DESKTOP_PANEL_ROOT / "prototype" / "index.html"


class PanelWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="AI-native Linux")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_default_size(460, 560)
        self.connect("destroy", Gtk.main_quit)

        self.webview = WebKit2.WebView()
        self.webview.get_settings().set_enable_developer_extras(True)
        self.webview.connect("load-changed", self.on_load_changed)
        self.webview.connect("load-failed", self.on_load_failed)
        self.webview.connect("web-process-terminated", self.on_web_process_terminated)
        self.webview.load_uri(PROTOTYPE_INDEX.as_uri())
        self.add(self.webview)

        self.show_all()
        self.resize(460, 560)
        GLib.idle_add(self.place_at_bottom_right)

    def on_load_changed(self, _webview, event):
        if event == WebKit2.LoadEvent.FINISHED:
            self.webview.run_javascript(
                "document.body.classList.add('native-shell');",
                None,
                None,
                None,
            )

    def on_load_failed(self, _webview, _event, failing_uri, error):
        print(f"WebKit не загрузил {failing_uri}: {error.message}", file=sys.stderr)
        return False

    def on_web_process_terminated(self, _webview, reason):
        print(f"WebKit-процесс завершился: {reason}", file=sys.stderr)

    def place_at_bottom_right(self):
        """Place the panel on X11; Wayland may let the compositor choose placement."""
        window = self.get_window()
        screen = self.get_screen()
        if window is None or screen is None:
            return False

        monitor = screen.get_monitor_at_window(window)
        geometry = screen.get_monitor_geometry(monitor)
        width, height = self.get_size()
        margin = 24
        self.move(
            geometry.x + geometry.width - width - margin,
            geometry.y + geometry.height - height - margin,
        )
        return False


def main():
    if not PROTOTYPE_INDEX.exists():
        raise SystemExit(f"Не найден интерфейс: {PROTOTYPE_INDEX}")

    Gtk.init([])
    PanelWindow()
    Gtk.main()


if __name__ == "__main__":
    main()
