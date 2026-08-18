import shutil
import sys
import unittest
from pathlib import Path
from uuid import uuid4

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


PROJECT_ROOT = Path(__file__).resolve().parents[3]
for source in (
    PROJECT_ROOT / "services" / "storage-catalog" / "src",
    PROJECT_ROOT / "services" / "indexer" / "src",
    PROJECT_ROOT / "modules" / "documents-pdf" / "src",
):
    sys.path.insert(0, str(source))

from ai_native_query import DocumentQuery, QueryRuntimeApplication, QueryService
from ai_native_storage import VolumeRegistry
from ai_native_storage.contracts import DiscoveredVolume


class Discovery:
    def __init__(self, volume: DiscoveredVolume) -> None:
        self.volume = volume

    def discover(self):
        return [self.volume]


class QueryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / "tmp" / "query-service-tests" / str(uuid4())
        self.documents = self.root / "documents"
        self.documents.mkdir(parents=True)
        self.storage_db = self.root / "storage.sqlite3"
        self.index_db = self.root / "index.sqlite3"
        volume = DiscoveredVolume(
            "test-volume", "Documents", str(self.documents), "test", "testfs", True, False, False
        )
        VolumeRegistry(self.storage_db, discovery=Discovery(volume)).refresh()
        self.service = QueryService(
            storage_database=self.storage_db,
            index_database=self.index_db,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def make_pdf(self, text: str, name: str = "study.pdf") -> Path:
        path = self.documents / name
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_reference = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 14 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        with path.open("wb") as output:
            writer.write(output)
        return path

    def test_ingests_pdf_searches_content_and_saves_snapshot(self) -> None:
        self.make_pdf("Mathematics algebra geometry")
        self.make_pdf("Cooking recipes and ingredients", "cooking.pdf")
        self.service.catalog.scan_volume("test-volume")

        report = self.service.ingest_pdfs()
        results = self.service.search(DocumentQuery(text="algebra", extensions=("pdf",)))
        collection_id = self.service.save_snapshot("Math PDF", results)

        self.assertEqual(report.indexed, 2)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "study.pdf")
        self.assertIn("PDF page 1", results[0].snippet or "")
        self.assertEqual(len(self.service.collections.resolve(collection_id)), 1)

    def test_runtime_adapter_validates_payload(self) -> None:
        application = QueryRuntimeApplication(self.service)
        self.assertIn("documents.query.search", application.capabilities())
        self.assertIn("catalog", application.index_status())
        with self.assertRaises(ValueError):
            application.search({"text": [], "limit": 20})


if __name__ == "__main__":
    unittest.main()
