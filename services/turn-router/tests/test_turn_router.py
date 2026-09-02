import unittest

from ai_native_turns import TurnKind, TurnRequest, TurnRouter, TurnRoutingError


class Classifier:
    def __init__(self, payload):
        self.payload = payload

    def classify_turn(self, _request):
        return self.payload


def payload(kind, conversation=None, action=None, confidence=0.9):
    return {
        "kind": kind,
        "language": "ru",
        "confidence": confidence,
        "conversation_text": conversation,
        "action_text": action,
    }


class TurnRouterTests(unittest.TestCase):
    def test_leading_negation_is_conversation_without_model_classification(self):
        classifier = Classifier(payload("action", action="не ищи PDF"))
        for text in (
            "Не ищи PDF",
            "Ничего не меняй",
            "Do not copy the files",
            "Don't search my disk",
            "Never delete anything",
        ):
            with self.subTest(text=text):
                result = TurnRouter(classifier).route(TurnRequest(text, "ru"))
                self.assertEqual(result.kind, TurnKind.CONVERSATION)
                self.assertEqual(result.conversation_text, text)

    def test_not_only_phrase_is_not_mistaken_for_negative_guard(self):
        text = "Не только найди PDF, но и скопируй их"
        result = TurnRouter(
            Classifier(payload("action", action=text))
        ).route(TurnRequest(text, "ru"))

        self.assertEqual(result.kind, TurnKind.ACTION)

    def test_pure_conversation_uses_original_text_when_small_model_paraphrases(self):
        text = "А при какой температуре печь хлеб"
        result = TurnRouter(Classifier(payload(
            "conversation", "Вопрос о температуре хлеба", None
        ))).route(TurnRequest(text, "ru"))

        self.assertEqual(result.kind, TurnKind.CONVERSATION)
        self.assertEqual(result.conversation_text, text)

    def test_empty_optional_fragments_are_treated_as_json_null(self):
        text = "Привет"
        result = TurnRouter(Classifier(payload(
            "conversation", "", ""
        ))).route(TurnRequest(text, "ru"))

        self.assertEqual(result.kind, TurnKind.CONVERSATION)
        self.assertEqual(result.conversation_text, text)

    def test_pure_action_accepts_exact_original_text(self):
        text = "Найди все PDF на моём компьютере"
        result = TurnRouter(Classifier(payload(
            "action", None, text
        ))).route(TurnRequest(text, "ru"))

        self.assertEqual(result.kind, TurnKind.ACTION)
        self.assertEqual(result.action_text, text)

    def test_pure_action_uses_current_original_when_model_rewrites_fragments(self):
        text = "Найди мои учебные дкоументы"
        result = TurnRouter(Classifier(payload(
            "action",
            conversation="Найди мои учебные документы",
            action="Найди мои учебные документы",
        ))).route(TurnRequest(text, "ru"))

        self.assertEqual(result.kind, TurnKind.ACTION)
        self.assertIsNone(result.conversation_text)
        self.assertEqual(result.action_text, text)

    def test_accepts_mixed_exact_non_overlapping_fragments(self):
        text = "Расскажи про хлеб и найди все PDF"
        result = TurnRouter(Classifier(payload(
            "mixed", "Расскажи про хлеб", "найди все PDF"
        ))).route(TurnRequest(text, "ru"))
        self.assertEqual(result.kind, TurnKind.MIXED)

    def test_recovers_only_conversation_when_model_overlaps_exact_action(self):
        text = "При какой температуре печь хлеб и найди все PDF"
        result = TurnRouter(Classifier(payload(
            "mixed", text, "найди все PDF"
        ))).route(TurnRequest(text, "ru"))

        self.assertEqual(result.kind, TurnKind.MIXED)
        self.assertEqual(
            result.conversation_text, "При какой температуре печь хлеб и"
        )
        self.assertEqual(result.action_text, "найди все PDF")

    def test_accepts_english_conversation_and_action_split(self):
        text = "What temperature should I bake bread at, and find my PDF files"
        result = TurnRouter(Classifier(payload(
            "mixed",
            "What temperature should I bake bread at",
            "find my PDF files",
        ))).route(TurnRequest(text, "en"))
        self.assertEqual(result.kind, TurnKind.MIXED)
        self.assertEqual(result.action_text, "find my PDF files")

    def test_low_confidence_becomes_clarification_without_action(self):
        result = TurnRouter(Classifier(payload(
            "action", action="найди PDF", confidence=0.4
        ))).route(TurnRequest("найди PDF", "ru"))
        self.assertEqual(result.kind, TurnKind.CLARIFICATION)
        self.assertIsNone(result.action_text)

    def test_rejects_invented_or_overlapping_fragments(self):
        text = "Расскажи про хлеб и найди PDF"
        with self.assertRaises(TurnRoutingError):
            TurnRouter(Classifier(payload(
                "mixed", conversation="Расскажи про хлеб", action="удали всё"
            ))).route(
                TurnRequest(text, "ru")
            )

        # Conversation recovery cannot collapse two separated fragments around
        # an action in the middle of the message.
        text = "Расскажи про хлеб, найди PDF, пожалуйста"
        with self.assertRaises(TurnRoutingError):
            TurnRouter(Classifier(payload(
                "mixed", text, "найди PDF"
            ))).route(TurnRequest(text, "ru"))

    def test_rejects_shape_mismatch(self):
        with self.assertRaises(TurnRoutingError):
            TurnRouter(Classifier(payload("mixed", action="Привет"))).route(
                TurnRequest("Привет", "ru")
            )


if __name__ == "__main__":
    unittest.main()
