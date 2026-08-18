#!/usr/bin/env python3
"""Minimal GTK/WebKit shell for the desktop panel prototype."""

import os
import sys
import argparse
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")

from gi.repository import GLib, Gtk, WebKit2


DESKTOP_PANEL_ROOT = Path(__file__).resolve().parents[1]
PROTOTYPE_INDEX = DESKTOP_PANEL_ROOT / "prototype" / "index.html"


class PanelWindow(Gtk.Window):
    def __init__(self, self_test=False):
        super().__init__(title="AI-native Linux")
        self.self_test = self_test
        self.self_test_passed = None
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
        self.webview.load_uri(f"{PROTOTYPE_INDEX.as_uri()}?native=1")
        self.add(self.webview)

        self.show_all()
        self.resize(460, 560)
        GLib.idle_add(self.place_at_bottom_right)

    def on_load_changed(self, _webview, event):
        if event != WebKit2.LoadEvent.FINISHED or not self.self_test:
            return

        title = self.webview.get_title()
        uri = self.webview.get_uri()
        self.self_test_passed = bool(title and uri)
        print(f"SELF-TEST: WebKit loaded {uri} ({title})")
        Gtk.main_quit()

    def on_load_failed(self, _webview, _event, failing_uri, error):
        print(f"WebKit не загрузил {failing_uri}: {error.message}", file=sys.stderr)
        if self.self_test:
            self.self_test_passed = False
            Gtk.main_quit()
        return False

    def on_web_process_terminated(self, _webview, reason):
        print(f"WebKit-процесс завершился: {reason}", file=sys.stderr)
        if self.self_test:
            self.self_test_passed = False
            Gtk.main_quit()

    def place_at_bottom_right(self):
        """Place the panel on X11; Wayland may let the compositor choose placement."""
        if os.environ.get("XDG_SESSION_TYPE") == "wayland":
            return False

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
    parser = argparse.ArgumentParser(description="Запуск Linux-панели")
    parser.add_argument("--self-test", action="store_true", help="проверить загрузку панели и завершить")
    args = parser.parse_args()

    if not PROTOTYPE_INDEX.exists():
        raise SystemExit(f"Не найден интерфейс: {PROTOTYPE_INDEX}")

    Gtk.init([])
    window = PanelWindow(self_test=args.self_test)
    Gtk.main()

    if args.self_test and not window.self_test_passed:
        raise SystemExit("SELF-TEST FAILED: WebKit не загрузил интерфейс")


if __name__ == "__main__":
    main()
