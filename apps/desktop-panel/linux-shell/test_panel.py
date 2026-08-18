import unittest
from pathlib import Path


SHELL_DIR = Path(__file__).resolve().parent
DESKTOP_PANEL_ROOT = SHELL_DIR.parents[0]
PANEL_SOURCE = SHELL_DIR / "panel.py"
INDEX = DESKTOP_PANEL_ROOT / "prototype" / "index.html"
STYLES = DESKTOP_PANEL_ROOT / "prototype" / "styles.css"


class LinuxShellFilesTest(unittest.TestCase):
    def test_prototype_files_exist(self):
        self.assertTrue(INDEX.is_file())
        self.assertTrue(STYLES.is_file())

    def test_native_window_targets_prototype(self):
        source = PANEL_SOURCE.read_text(encoding="utf-8")
        self.assertIn('PROTOTYPE_INDEX = DESKTOP_PANEL_ROOT / "prototype" / "index.html"', source)
        self.assertIn("?native=1", source)

    def test_native_markup_and_styles_are_present(self):
        html = INDEX.read_text(encoding="utf-8")
        css = STYLES.read_text(encoding="utf-8")
        self.assertIn('id="panel-toggle"', html)
        self.assertIn("body.native-shell", css)
        self.assertIn(".panel-shell", css)


if __name__ == "__main__":
    unittest.main()
