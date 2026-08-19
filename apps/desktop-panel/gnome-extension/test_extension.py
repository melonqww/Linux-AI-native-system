import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parent


class GnomeExtensionFilesTest(unittest.TestCase):
    def test_metadata_is_valid(self):
        metadata = json.loads((ROOT / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["uuid"], "ai-native-linux@melonqww")
        self.assertIn("46", metadata["shell-version"])

    def test_runtime_files_exist(self):
        for filename in ("extension.js", "stylesheet.css", "install.sh", "README.md", "TROUBLESHOOTING.md"):
            self.assertTrue((ROOT / filename).is_file(), filename)

    def test_install_reloads_live_extension_before_copy(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('gnome-extensions disable "${EXTENSION_UUID}"', script)
        self.assertIn('gnome-extensions enable "${EXTENSION_UUID}"', script)

    def test_native_panel_contract_is_present(self):
        source = (ROOT / "extension.js").read_text(encoding="utf-8")
        for marker in (
            "Main.layoutManager.addChrome",
            "monitors-changed",
            "this._scroll.set_child(this._messages)",
            "monitor.height * 0.52",
            "this._toggleLabel.set_text(this._collapsed ? '‹' : '›')",
            "this._collapsedTranslation = PANEL_WIDTH + PANEL_HORIZONTAL_MARGIN",
            "const toggleTarget = this._collapsed ? -4 : 0",
            "preferences-system-symbolic",
            "folder-symbolic",
            "const resizeEntry = () =>",
            "entry.clutter_text.editable = true",
            "entry.clutter_text.single_line_mode = false",
            "_animateHighlight",
            "Qwen 3.5 2B",
            "Рабочая область",
            "Сообщение для вашего ИИ",
            "Runtime transport will be plugged back in",
            "this._stylesheet = this.dir.get_child('stylesheet.css')",
            "this._theme.load_stylesheet(this._stylesheet)",
            "this._theme.unload_stylesheet(this._stylesheet)",
            "stylesheet load failed",
            "new ChatView(this._runtime)",
            "const setModelMenuOpen = open =>",
            "modelChevron.set_text(open ? '⌃' : '⌄')",
            "const SidebarView = GObject.registerClass",
            "this._sidebar = new SidebarView()",
            "Состояние системы",
            "Мини-диспетчер задач",
            "Быстрые системные действия",
            "Последние действия",
            "createMetricRing(37)",
            "this._workspace = new ChatView(this._runtime)",
            "_attachFallback(error)",
            "panel construction failed",
        ):
            self.assertIn(marker, source)

    def test_panel_surface_does_not_paint_behind_toggle(self):
        stylesheet = (ROOT / "stylesheet.css").read_text(encoding="utf-8")
        self.assertIn(".ai-native-shell", stylesheet)
        self.assertIn("background-color: transparent;", stylesheet)
        self.assertIn(".ai-panel-content", stylesheet)
        self.assertIn("box-shadow: 0 18px 52px", stylesheet)
        self.assertIn("margin-top: 5px", stylesheet)
        self.assertIn("border-radius: 22px;", stylesheet)
        self.assertIn("font-weight: 700;", stylesheet)
        self.assertIn("border-radius: 21px 21px 0 0;", stylesheet)
        self.assertIn("margin: 0 7px 6px 4px;", stylesheet)
        self.assertIn("max-height: 78px;", stylesheet)
        self.assertIn("margin-top: 2px;", stylesheet)


if __name__ == "__main__":
    unittest.main()
