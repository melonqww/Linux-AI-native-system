"""Autonomous File Search R1 lab over temporary multi-volume storage."""

import shutil
import sys
import unittest
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


PROJECT_ROOT = Path(__file__).resolve().parents[3]
for source in (
    PROJECT_ROOT / "services" / "storage-catalog" / "src",
    PROJECT_ROOT / "services" / "indexer" / "src",
    PROJECT_ROOT / "services" / "index-scheduler" / "src",
    PROJECT_ROOT / "modules" / "documents-pdf" / "src",
):
    sys.path.insert(0, str(source))

from ai_native_query import ContentAvailability, DocumentQuery, QueryService, SearchMode
from ai_native_scheduler import IndexScheduler
from ai_native_storage import PermissionLevel, VolumeRegistry
from ai_native_storage.contracts import DiscoveredVolume


class Discovery:
    def __init__(self, volumes):
        self.volumes = volumes

    def discover(self):
        return list(self.volumes)


class FileSearchR1LabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / "tmp" / "file-search-r1" / str(uuid4())
        self.system = self.root / "system"
        self.external = self.root / "external"
        self.system.mkdir(parents=True)
        self.external.mkdir()
        self.storage_db = self.root / "storage.sqlite3"
        self.index_db = self.root / "index.sqlite3"
        self.volumes = (
            DiscoveredVolume(
                "system", "System", str(self.system), "system", "testfs",
                True, False, False,
            ),
            DiscoveredVolume(
                "external", "External", str(self.external), "external", "testfs",
                False, True, False,
            ),
        )
        registry = VolumeRegistry(self.storage_db, discovery=Discovery(self.volumes))
        registry.refresh()
        registry.set_permission("external", PermissionLevel.METADATA)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def make_pdf(path: Path, text: str) -> None:
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        reference = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): reference})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 14 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        with path.open("wb") as output:
            writer.write(output)

    @staticmethod
    def finish(scheduler: IndexScheduler) -> None:
        for cycle in range(2_000):
            status = scheduler.process_once(now=float(cycle))
            if status.coverage_complete:
                return
        raise AssertionError("bounded R1 crawl did not complete")

    def test_multi_volume_large_and_broken_files_remain_truthful(self) -> None:
        self.make_pdf(self.system / "mathematics.pdf", "Linear algebra theorem")
        (self.system / "broken.pdf").write_bytes(b"%PDF-1.7\ntruncated")
        large = self.system / "large-notes.txt"
        large.write_text("largefiletoken\n" + "x" * (2 * 1_048_576), encoding="utf-8")
        external_note = self.external / "external-notes.txt"
        external_note.write_text("externalcontenttoken", encoding="utf-8")

        scheduler = IndexScheduler(
            storage_database=self.storage_db,
            index_database=self.index_db,
        )
        scheduler.request_rescan("system")
        scheduler.request_rescan("external")
        self.finish(scheduler)
        query = QueryService(
            storage_database=self.storage_db,
            index_database=self.index_db,
            coverage_source=lambda: asdict(scheduler.status()),
        )

        pdfs = query.search(
            DocumentQuery(mode=SearchMode.METADATA, extensions=("pdf",), limit=10)
        )
        by_name = {result.name: result for result in pdfs}

        self.assertEqual(set(by_name), {"mathematics.pdf", "broken.pdf"})
        self.assertEqual(
            by_name["mathematics.pdf"].content_state, ContentAvailability.INDEXED
        )
        self.assertEqual(by_name["broken.pdf"].content_state, ContentAvailability.UNAVAILABLE)
        self.assertEqual(by_name["broken.pdf"].content_reason, "damaged")
        self.assertEqual(
            [result.name for result in query.search(DocumentQuery(text="largefiletoken"))],
            ["large-notes.txt"],
        )
        external_metadata = query.search(
            DocumentQuery(
                mode=SearchMode.METADATA,
                name_contains=("external-notes",),
                volume_ids=("external",),
            )
        )
        self.assertEqual(len(external_metadata), 1)
        self.assertEqual(
            external_metadata[0].content_state, ContentAvailability.NOT_PERMITTED
        )
        self.assertEqual(
            query.search(
                DocumentQuery(
                    mode=SearchMode.CONTENT,
                    text="externalcontenttoken",
                    volume_ids=("external",),
                )
            ),
            [],
        )
        coverage = query.coverage(("system", "external"))
        self.assertTrue(coverage.complete)
        self.assertEqual(set(coverage.covered_volume_ids), {"system", "external"})
        self.assertGreaterEqual(coverage.cataloged_items, 4)


if __name__ == "__main__":
    unittest.main()
