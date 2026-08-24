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


if __name__ == "__main__":
    unittest.main()
