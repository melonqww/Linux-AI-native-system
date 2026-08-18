import shutil
import sys
import unittest
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ai_native_applications import ApplicationRegistry


class TestApplications(unittest.TestCase):
    def test_finds_desktop_application(self):
        root = Path(__file__).resolve().parents[3] / "tmp" / "application-tests" / str(uuid4())
        root.mkdir(parents=True)
        try:
            (root / "discord.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Discord\nExec=/usr/bin/discord %U\n",
                encoding="utf-8",
            )
            result = ApplicationRegistry((root,)).find("discord")
            self.assertEqual(result[0].name, "Discord")
            self.assertIn("/usr/bin/discord", result[0].executable)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
