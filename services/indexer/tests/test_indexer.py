"""Integration tests for the local opt-in index."""

import shutil
import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

from ai_native_indexer.chunking import chunk_text
from ai_native_indexer.contracts import ContentIndexState
from ai_native_indexer.file_policy import FilePolicy
from ai_native_indexer.service import IndexerService


class IndexerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_root = Path(__file__).resolve().parents[3] / "tmp" / "indexer-tests"
        self.base = temporary_root / str(uuid4())
        self.base.mkdir(parents=True)
        self.root = self.base / "allowed"
        self.root.mkdir()
        self.service = IndexerService(self.base / "index.sqlite3")

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def test_indexes_and_finds_text_with_source_lines(self) -> None:
        document = self.root / "ubuntu.md"
        document.write_text(
            "# Настройка Ubuntu\n\nДля диагностики сети откройте системную панель.\n",
            encoding="utf-8",
        )

        report = self.service.index_directory(self.root)
        hits = self.service.search("диагностики сети")

        self.assertEqual(report.indexed, 1)
        self.assertEqual(
            self.service.get_index_status(),
            {"sources": 1, "chunks": 1, "unavailable": 0, "unsupported": 0},
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(Path(hits[0].path), document.resolve())
        self.assertEqual(hits[0].line_start, 1)
        self.assertGreaterEqual(hits[0].line_end, 3)

    def test_second_pass_does_not_read_unchanged_file(self) -> None:
        document = self.root / "notes.txt"
        document.write_text("неизменяемая заметка", encoding="utf-8")
        self.service.index_directory(self.root)

        original_read_bytes = Path.read_bytes

        def fail_for_document(path: Path) -> bytes:
            if path == document:
                raise AssertionError("unchanged file was read")
            return original_read_bytes(path)

        with mock.patch.object(Path, "read_bytes", fail_for_document):
            report = self.service.index_directory(self.root)

        self.assertEqual(report.unchanged, 1)

    def test_removes_deleted_source_and_search_chunks(self) -> None:
        document = self.root / "temporary.md"
        document.write_text("редкое слово архипелаг", encoding="utf-8")
        self.service.index_directory(self.root)
        document.unlink()

        report = self.service.index_directory(self.root)

        self.assertEqual(report.removed, 1)
        self.assertEqual(self.service.search("архипелаг"), [])
        self.assertEqual(self.service.content_statuses([str(document.resolve())]), {})
        self.assertEqual(
            self.service.get_index_status(),
            {"sources": 0, "chunks": 0, "unavailable": 0, "unsupported": 0},
        )

    def test_replaces_changed_content_without_stale_search_results(self) -> None:
        document = self.root / "changing.md"
        document.write_text("первоначальный вулкан", encoding="utf-8")
        self.service.index_directory(self.root)
        document.write_text("обновлённый океан и дополнительный текст", encoding="utf-8")

        report = self.service.index_directory(self.root)

        self.assertEqual(report.updated, 1)
        self.assertEqual(self.service.search("вулкан"), [])
        self.assertEqual(len(self.service.search("океан")), 1)

    def test_existing_index_sources_are_backfilled_with_indexed_state(self) -> None:
        document = self.root / "existing.txt"
        document.write_text("existing content", encoding="utf-8")
        self.service.index_directory(self.root)
        with self.service.storage.connect() as connection:
            connection.execute("DELETE FROM source_states")

        states = IndexerService(self.base / "index.sqlite3").content_statuses(
            [str(document.resolve())]
        )

        self.assertEqual(states[str(document.resolve())].state, ContentIndexState.INDEXED)

    def test_excludes_secrets_hidden_directories_and_unsupported_files(self) -> None:
        (self.root / ".env").write_text("TOKEN=secret", encoding="utf-8")
        (self.root / "private.pem").write_text("secret", encoding="utf-8")
        (self.root / "photo.png").write_bytes(b"not really an image")
        git_directory = self.root / ".git"
        git_directory.mkdir()
        (git_directory / "history.md").write_text("must not be indexed", encoding="utf-8")

        report = self.service.index_directory(self.root)

        self.assertEqual(report.indexed, 0)
        self.assertEqual(report.skipped["sensitive_file"], 2)
        self.assertEqual(report.skipped["unsupported_type"], 1)
        self.assertEqual(report.skipped["excluded_directory"], 1)

    def test_file_size_limit_is_configurable(self) -> None:
        document = self.root / "large.txt"
        document.write_text("данные для индекса", encoding="utf-8")
        strict_service = IndexerService(
            self.base / "strict.sqlite3",
            file_policy=FilePolicy(max_file_bytes=5),
        )

        report = strict_service.index_directory(self.root)

        self.assertEqual(report.skipped, {"too_large": 1})
        state = strict_service.content_statuses([str(document.resolve())])[str(document.resolve())]
        self.assertEqual(state.state, ContentIndexState.UNAVAILABLE)
        self.assertEqual(state.reason, "too_large")

    def test_failed_reindex_removes_stale_searchable_content(self) -> None:
        document = self.root / "changing.txt"
        document.write_text("searchable volcano", encoding="utf-8")
        strict_service = IndexerService(
            self.base / "strict-reindex.sqlite3",
            file_policy=FilePolicy(max_file_bytes=32),
        )
        strict_service.index_directory(self.root)
        document.write_text("volcano " + "x" * 100, encoding="utf-8")

        report = strict_service.index_directory(self.root)

        self.assertEqual(report.skipped, {"too_large": 1})
        self.assertEqual(strict_service.search("volcano"), [])
        self.assertEqual(strict_service.get_index_status()["unavailable"], 1)

    def test_rejects_invalid_search_limit_and_handles_punctuation(self) -> None:
        self.assertEqual(self.service.search("?!"), [])
        with self.assertRaises(ValueError):
            self.service.search("query", limit=0)

    def test_multiple_query_terms_increase_recall(self) -> None:
        (self.root / "security.md").write_text("правила безопасности", encoding="utf-8")
        (self.root / "permissions.md").write_text("проверка разрешений", encoding="utf-8")
        self.service.index_directory(self.root)

        hits = self.service.search("безопасности разрешений")

        self.assertEqual(len(hits), 2)

    def test_chunk_overlap_starts_on_text_boundary(self) -> None:
        text = "первая строка документа\nвторая строка документа\nтретья строка документа"

        chunks = chunk_text(text, target_chars=35, overlap_chars=12)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.content.startswith(("первая", "вторая", "третья")) for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
