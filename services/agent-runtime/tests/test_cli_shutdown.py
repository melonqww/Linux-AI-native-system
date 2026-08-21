import unittest

from ai_native_linux.cli import _interrupt_runtime


class RuntimeShutdownTests(unittest.TestCase):
    def test_sigterm_handler_unwinds_runtime_for_worker_cleanup(self):
        with self.assertRaises(KeyboardInterrupt):
            _interrupt_runtime(15, None)


if __name__ == "__main__":
    unittest.main()
