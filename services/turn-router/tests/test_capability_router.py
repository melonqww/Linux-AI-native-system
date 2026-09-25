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
            "посмотреть PDF-файлы на компьютере",
            "нужны только PDF по математике",
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

    def test_negation_does_not_cross_a_clause_boundary(self):
        for text in (
            "Ничего лишнего не делай: Найди мои учебные документы",
            (
                "Пожалуйста, будь внимателен и ничего лишнего не делай: "
                "Нет, нужны только PDF по математике"
            ),
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    self.router.requested_operations(text),
                    ("search_documents",),
                )

    def test_negation_in_the_same_clause_still_blocks_the_operation(self):
        self.assertEqual(
            self.router.requested_operations("Ничего не найди в документах"),
            (),
        )

    def test_repeated_natural_request_uses_module_owned_cue(self):
        self.assertEqual(
            self.router.requested_operations(
                "Жалко, но ты можешь снова посмотреть PDF-файлы, "
                "а также сказать, при какой температуре печь хлеб?"
            ),
            ("search_documents",),
        )

    def test_required_operations_disambiguate_shared_imperatives(self):
        file_actions = (
            CapabilityDescriptor(
                "files.directory.create", "create_directory", "Create a folder.",
                ("создай папку Проекты на рабочем столе",),
            ),
            CapabilityDescriptor(
                "storage.materialize.plan-copy", "copy_results", "Copy files.",
                ("создай папку и скопируй туда найденные файлы",),
            ),
            CapabilityDescriptor(
                "files.items.move", "move_results", "Move files.",
                ("перемести найденные файлы в папку на рабочем столе",),
            ),
            CapabilityDescriptor(
                "files.items.trash", "trash_results", "Trash files.",
                ("перемести найденные файлы в корзину",),
            ),
        )
        router = CapabilityCandidateRouter(file_actions)
        self.assertEqual(
            router.required_operations("Создай на рабочем столе папку Проекты"),
            ("create_directory",),
        )
        self.assertEqual(
            router.required_operations("Перемести найденный файл в папку Готово на рабочем столе"),
            ("move_results",),
        )
        self.assertEqual(
            router.required_operations("Перемести найденный файл в корзину"),
            ("trash_results",),
        )
        self.assertEqual(
            router.required_operations("Создай папку и скопируй туда найденные файлы"),
            ("copy_results",),
        )
        self.assertEqual(
            router.required_operations(
                "Не перемещай файлы в корзину; перемести их в папку на рабочем столе"
            ),
            ("move_results",),
        )

    def test_incidental_question_word_does_not_require_file_inspection(self):
        router = CapabilityCandidateRouter((
            CapabilityDescriptor(
                "files.items.inspect", "inspect_files", "Inspect file metadata.",
                ("какой размер у этого файла",),
            ),
            CapabilityDescriptor(
                "documents.query.search", "search_documents", "Search files.",
                ("найди все PDF-файлы на моём компьютере",),
            ),
        ))

        self.assertEqual(
            router.required_operations(
                "При какой температуре печь хлеб и найди все PDF-файлы на моём компьютере"
            ),
            ("search_documents",),
        )


if __name__ == "__main__":
    unittest.main()
