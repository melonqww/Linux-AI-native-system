#!/usr/bin/env python3
"""Minimal GTK/WebKit shell for the desktop panel prototype."""

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")

from gi.repository import Gdk, GLib, Gtk, WebKit2


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROTOTYPE_INDEX = PROJECT_ROOT / "prototype" / "index.html"


class PanelWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="AI-native Linux")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_default_size(460, 560)
        self.set_size_request(320, 420)
        self.connect("destroy", Gtk.main_quit)

        self.webview = WebKit2.WebView()
        self.webview.get_settings().set_enable_developer_extras(True)
        self.webview.load_uri(PROTOTYPE_INDEX.as_uri())
        self.add(self.webview)

        self.show_all()
        GLib.idle_add(self.place_at_bottom_right)

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
