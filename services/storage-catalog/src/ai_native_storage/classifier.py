"""Deterministic file roles and sensitivity labels."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from .contracts import EntryType


SENSITIVE_NAMES = frozenset(
    {
        ".env",
        "credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "known_hosts",
    }
)
SENSITIVE_EXTENSIONS = frozenset({".key", ".kdbx", ".p12", ".pfx", ".pem"})
SENSITIVE_DIRECTORIES = frozenset(
    {
        ".gnupg",
        ".ssh",
        "keyrings",
        "password-store",
        "secrets",
    }
)
PROJECT_MARKERS = {
    "pyproject.toml": "python_project",
    "requirements.txt": "python_project",
    "package.json": "node_project",
    "Cargo.toml": "rust_project",
    "go.mod": "go_project",
    ".git": "source_repository",
}
SOURCE_EXTENSIONS = frozenset(
    {".c", ".cc", ".cpp", ".cs", ".go", ".java", ".js", ".kt", ".py", ".rs", ".sh", ".ts"}
)
CONFIG_EXTENSIONS = frozenset({".conf", ".ini", ".json", ".toml", ".yaml", ".yml"})
ARCHIVE_EXTENSIONS = frozenset({".7z", ".bz2", ".gz", ".rar", ".tar", ".xz", ".zip"})
APPLICATION_EXTENSIONS = frozenset({".appimage", ".desktop", ".exe"})


def is_sensitive(path: Path) -> bool:
    name = path.name.casefold()
    parts = {part.casefold() for part in path.parts}
    return (
        name in SENSITIVE_NAMES
        or name.startswith(".env.")
        or path.suffix.casefold() in SENSITIVE_EXTENSIONS
        or bool(parts & SENSITIVE_DIRECTORIES)
    )


def mime_type_for(path: Path, entry_type: EntryType) -> str | None:
    if entry_type is not EntryType.FILE:
        return None
    mime_type, _encoding = mimetypes.guess_type(path.name, strict=False)
    return mime_type


def role_for(path: Path, entry_type: EntryType, mime_type: str | None) -> str:
    parts = {part.casefold() for part in path.parts}
    suffix = path.suffix.casefold()

    if entry_type is EntryType.SYMLINK:
        return "link"
    if entry_type is EntryType.DIRECTORY:
        if path.name.casefold() in {"downloads", "загрузки"}:
            return "downloads"
        if path.name.casefold() in {"desktop", "рабочий стол"}:
            return "desktop"
        if path.name.casefold() in {"documents", "документы"}:
            return "documents"
        return "directory"
    if ".config" in parts or "etc" in parts:
        return "configuration"
    if "var" in parts and "log" in parts or suffix == ".log":
        return "log"
    if ".cache" in parts or "cache" in parts:
        return "cache"
    if suffix in APPLICATION_EXTENSIONS:
        return "application"
    if suffix in SOURCE_EXTENSIONS:
        return "source_code"
    if suffix in CONFIG_EXTENSIONS:
        return "configuration"
    if suffix in ARCHIVE_EXTENSIONS:
        return "archive"
    if mime_type:
        category = mime_type.partition("/")[0]
        if category in {"audio", "image", "video"}:
            return category
        if mime_type == "application/pdf" or category == "text":
            return "document"
    return "file"


def project_role_for_names(names: set[str]) -> str | None:
    for marker, role in PROJECT_MARKERS.items():
        if marker in names:
            return role
    return None
