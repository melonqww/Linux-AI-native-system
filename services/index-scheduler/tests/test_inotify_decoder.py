import struct
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
for source in (
    PROJECT_ROOT / "services" / "storage-catalog" / "src",
    PROJECT_ROOT / "services" / "indexer" / "src",
    PROJECT_ROOT / "services" / "index-scheduler" / "src",
):
    sys.path.insert(0, str(source))

from ai_native_scheduler.contracts import EventKind
from ai_native_scheduler.inotify import IN_CLOSE_WRITE, IN_ISDIR, decode_inotify_events


class InotifyDecoderTests(unittest.TestCase):
    def test_decodes_kernel_record_without_linux_runtime(self) -> None:
        name = b"notes.txt\0"
        name += b"\0" * ((4 - len(name) % 4) % 4)
        payload = struct.pack("iIII", 7, IN_CLOSE_WRITE | IN_ISDIR, 0, len(name)) + name
        decoded = decode_inotify_events(
            payload,
            {7: ("volume", Path("/documents"))},
            observed_at=12.5,
        )
        event, is_directory = decoded[0]
        self.assertEqual(event.kind, EventKind.MODIFIED)
        self.assertEqual(event.path, str(Path("/documents") / "notes.txt"))
        self.assertTrue(is_directory)


if __name__ == "__main__":
    unittest.main()
