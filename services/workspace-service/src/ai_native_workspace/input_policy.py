"""Text-channel attachment facts and defensive presentation checks (no tools)."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class WorkspaceAttachment:
    kind: str
    name: str

    def __post_init__(self):
        if self.kind not in {"image", "document", "audio", "video"}:
            raise ValueError("unsupported attachment kind")
        if (
            not isinstance(self.name, str)
            or not 1 <= len(self.name) <= 200
            or any(ord(c) < 32 for c in self.name)
        ):
            raise ValueError("invalid attachment name")


def unavailable_input(text: str, attachments: tuple) -> bool:
    # Legacy markers remain data: they cannot masquerade as decoded image bytes.
    return bool(attachments) or bool(
        re.search(
            r"\[[^\]\n]{0,100}(?:attachment|photo|image)[^\]\n]{0,100}\]", text, re.I
        )
    )


def input_notice(locale: str) -> str:
    return (
        "В этом канале вложение недоступно для обработки. Действия не запущены. Отправь задачу отдельным текстовым сообщением: я не могу достоверно сказать, что на вложении изображено."
        if locale.startswith("ru")
        else "The attachment is not available for processing in this channel. No actions were started. Please send the task as a separate text message; I cannot reliably say what the attachment shows."
    )


def safe_chat_reply(reply: str, locale: str) -> str:
    # An additional guard, not a claim of complete semantic verification. Task
    # result messages are never passed here and retain their trusted facts.
    if re.search(
        r"\b(?:я\s+)?(?:отфильтровал[аи]?|скопировал[аи]?|создал[аи]?|переместил[аи]?|наш[её]л|нашла)\b|\bI\s+(?:(?:have|just|already)\s+)*(?:found|copied|filtered|created|moved|deleted)\b",
        reply,
        re.I,
    ):
        return (
            "В этом ответе я не выполнял действий с файлами. Уточни, что нужно сделать."
            if locale.startswith("ru")
            else "I have not performed any file operation in this reply. Please specify the action you want."
        )
    return reply
