"""Model-neutral, non-executing validation boundary for workspace turn routing."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from .contracts import TurnClassification, TurnKind, TurnRequest


class TurnClassifier(Protocol):
    def classify_turn(self, request: TurnRequest) -> Mapping[str, object]: ...


class TurnRoutingError(ValueError):
    pass


class TurnRouter:
    def __init__(self, classifier: TurnClassifier, *, minimum_confidence: float = 0.65):
        if not 0.5 <= minimum_confidence <= 1:
            raise ValueError("minimum_confidence must be between 0.5 and 1")
        self.classifier = classifier
        self.minimum_confidence = minimum_confidence

    def route(self, request: TurnRequest) -> TurnClassification:
        payload = self.classifier.classify_turn(request)
        if not isinstance(payload, Mapping):
            raise TurnRoutingError("turn classification must be an object")
        required = {
            "kind", "language", "confidence", "conversation_text", "action_text"
        }
        if set(payload) != required:
            raise TurnRoutingError("turn classification fields are invalid")
        try:
            kind = TurnKind(self._text(payload["kind"], "kind", maximum=32))
        except ValueError as error:
            raise TurnRoutingError("turn kind is invalid") from error
        language = self._text(payload["language"], "language", maximum=16)
        confidence = payload["confidence"]
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= float(confidence) <= 1
        ):
            raise TurnRoutingError("turn confidence is invalid")
        conversation = self._optional_text(payload["conversation_text"], "conversation_text")
        action = self._optional_text(payload["action_text"], "action_text")
        # Pure turns do not need model-authored extraction: their only trusted
        # fragment is the complete user message. Small local models frequently
        # paraphrase the fragment even when the kind itself is correct.
        if kind is TurnKind.CONVERSATION and action is None:
            conversation = request.user_text
        if kind is TurnKind.MIXED and action is not None:
            conversation = self._recover_conversation_fragment(
                request.user_text, conversation, action
            )
        self._validate_fragments(request.user_text, kind, conversation, action)
        if float(confidence) < self.minimum_confidence:
            return TurnClassification(
                TurnKind.CLARIFICATION, language, float(confidence)
            )
        return TurnClassification(kind, language, float(confidence), conversation, action)

    @staticmethod
    def _recover_conversation_fragment(
        original: str, conversation: str | None, action: str
    ) -> str | None:
        """Recover only the non-executing side when a small model overlaps it.

        The action must already be an exact user-authored substring. We never
        derive or broaden an executable fragment from model output.
        """
        folded = original.casefold()
        action_start = folded.find(action.casefold())
        if action_start < 0:
            return conversation
        if conversation is not None:
            conversation_start = folded.find(conversation.casefold())
            if conversation_start >= 0:
                conversation_end = conversation_start + len(conversation)
                action_end = action_start + len(action)
                if conversation_end <= action_start or action_end <= conversation_start:
                    return conversation
        prefix = original[:action_start].strip()
        suffix = original[action_start + len(action) :].strip()
        # A single TurnClassification fragment must stay contiguous. If the
        # action sits in the middle, there is no safe one-fragment recovery.
        if bool(prefix) == bool(suffix):
            return conversation
        return prefix or suffix

    @staticmethod
    def _validate_fragments(
        original: str,
        kind: TurnKind,
        conversation: str | None,
        action: str | None,
    ) -> None:
        if conversation is not None and conversation.casefold() not in original.casefold():
            raise TurnRoutingError("conversation fragment is absent from the user message")
        if action is not None and action.casefold() not in original.casefold():
            raise TurnRoutingError("action fragment is absent from the user message")
        expected = {
            TurnKind.CONVERSATION: (True, False),
            TurnKind.ACTION: (False, True),
            TurnKind.MIXED: (True, True),
            TurnKind.CLARIFICATION: (False, False),
        }[kind]
        if (conversation is not None, action is not None) != expected:
            raise TurnRoutingError("turn fragments do not match turn kind")
        if conversation is not None and action is not None:
            conversation_start = original.casefold().find(conversation.casefold())
            action_start = original.casefold().find(action.casefold())
            conversation_range = range(conversation_start, conversation_start + len(conversation))
            action_range = range(action_start, action_start + len(action))
            if set(conversation_range).intersection(action_range):
                raise TurnRoutingError("turn fragments overlap")

    @staticmethod
    def _text(value: object, label: str, *, maximum: int) -> str:
        if not isinstance(value, str):
            raise TurnRoutingError(f"{label} must be text")
        text = value.strip()
        if not text or len(text) > maximum:
            raise TurnRoutingError(f"{label} is invalid")
        return text

    @classmethod
    def _optional_text(cls, value: object, label: str) -> str | None:
        # Small local models commonly emit "" instead of JSON null for an
        # absent optional fragment. Both representations mean "not present".
        return None if value is None or value == "" else cls._text(
            value, label, maximum=4_000
        )
