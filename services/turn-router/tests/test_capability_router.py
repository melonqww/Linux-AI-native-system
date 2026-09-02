import unittest

from ai_native_turns import CapabilityCandidateRouter, CapabilityDescriptor


DESCRIPTORS = (
    CapabilityDescriptor(
        "documents.query.search",
        "search_documents",
        "Find local files and PDF documents on computer disks.",
        (
            "найди все PDF файлы на компьютере",
            "покажи мои документы",
            "find local documents",
        ),
    ),
    CapabilityDescriptor(
        "storage.materialize.plan-copy",
        "copy_results",
        "Create a folder and copy previously found files.",
        (
            "создай папку и скопируй туда найденные файлы",
            "copy found files to desktop",
        ),
    ),
)


class CapabilityCandidateRouterTests(unittest.TestCase):
    def setUp(self):
        self.router = CapabilityCandidateRouter(DESCRIPTORS)

    def test_plain_conversation_has_no_system_candidate(self):
        for text in (
            "Привет",
            "Как у тебя дела?",
            "Какая температура нужна для выпекания хлеба?",
            "Так а что ты можешь в целом и какой ты ИИ",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.router.candidates(text), ())

    def test_mixed_workspace_message_finds_document_search(self):
        matches = self.router.candidates(
            "Очень круто, а теперь можешь найти все PDF файлы у меня на ПК"
        )

        self.assertTrue(matches)
        self.assertEqual(matches[0].operation, "search_documents")

    def test_compound_search_and_copy_selects_both_module_routes(self):
        matches = self.router.candidates(
            "Найди PDF файлы, создай папку и скопируй туда найденные файлы"
        )

        self.assertEqual(
            {match.operation for match in matches},
            {"search_documents", "copy_results"},
        )

    def test_typo_and_verbose_text_keep_document_candidate(self):
        for text in (
            "Найди мои учебные дкоументы",
            (
                "Если получится, помоги мне со следующим: "
                "найди мои учебные документы. Заранее большое спасибо."
            ),
        ):
            with self.subTest(text=text):
                matches = self.router.candidates(text)
                self.assertTrue(matches)
                self.assertEqual(matches[0].operation, "search_documents")

    def test_related_talk_may_be_a_candidate_but_never_an_execution_decision(self):
        matches = self.router.candidates("Я люблю читать документы")

        self.assertTrue(matches)
        self.assertEqual(matches[0].operation, "search_documents")

    def test_exposes_only_module_declared_operations_for_semantic_fallback(self):
        self.assertEqual(
            self.router.available_operations(),
            ("search_documents", "copy_results"),
        )


if __name__ == "__main__":
    unittest.main()
