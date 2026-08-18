import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from ai_native_pdf import PdfExtractionError, PdfExtractor


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class PdfExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / "tmp" / "pdf-extractor-tests" / str(uuid4())
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def make_pdf(self, text: str) -> Path:
        path = self.root / "mathematics.pdf"
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

    def test_extracts_page_aware_text(self) -> None:
        result = PdfExtractor().extract(self.make_pdf("Mathematics algebra geometry"))

        self.assertEqual(result.page_count, 1)
        self.assertIn("Mathematics algebra geometry", result.text)
        self.assertIn("[PDF page 1]", result.text)

    def test_rejects_non_pdf(self) -> None:
        path = self.root / "notes.txt"
        path.write_text("text", encoding="utf-8")
        with self.assertRaises(PdfExtractionError):
            PdfExtractor().extract(path)


if __name__ == "__main__":
    unittest.main()
