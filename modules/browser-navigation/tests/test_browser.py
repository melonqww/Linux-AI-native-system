import sys, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ai_native_browser import BrowserService

class TestBrowser(unittest.TestCase):
    def test_rejects_credentials_and_opens_valid_url(self):
        service = BrowserService()
        with self.assertRaises(ValueError): service.plan_open("https://user:pass@example.com")
        plan = service.plan_open("https://python.org/")
        with patch("webbrowser.open", return_value=True) as opened:
            self.assertTrue(service.open(plan))
        opened.assert_called_once()

    def test_builds_encoded_web_search_plan(self):
        plan = BrowserService().plan_search("linear algebra pdf")
        self.assertIn("linear+algebra+pdf", plan.url)
        self.assertFalse(plan.approval_required)


if __name__ == "__main__":
    unittest.main()
