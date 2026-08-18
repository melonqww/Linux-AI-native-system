#!/usr/bin/env python3
"""Minimal GTK/WebKit shell for the desktop panel prototype."""

import os
from pathlib import Path

# GTK3 cannot move a normal Wayland window. XWayland keeps this prototype
# positionable until the GNOME Shell integration is implemented.
if os.environ.get("XDG_SESSION_TYPE") == "wayland":
    os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")

from gi.repository import Gdk, GLib, Gtk, WebKit2


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

        screen = self.get_screen()
        if screen is not None and screen.is_composited():
            visual = screen.get_rgba_visual()
            if visual is not None:
                self.set_visual(visual)

        self.webview = WebKit2.WebView()
        background = Gdk.RGBA()
        background.red = 0
        background.green = 0
        background.blue = 0
        background.alpha = 0
        self.webview.set_background_color(background)
        self.webview.get_settings().set_enable_developer_extras(True)
        self.webview.load_uri(f"{PROTOTYPE_INDEX.as_uri()}?native=1")
        self.add(self.webview)

        self.show_all()
        self.resize(460, 560)
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
