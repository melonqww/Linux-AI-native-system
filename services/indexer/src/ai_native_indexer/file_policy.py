"""Rules that keep private, unsafe and irrelevant files out of the index."""

from dataclasses import dataclass
from pathlib import Path


DEFAULT_EXTENSIONS = frozenset({".md", ".txt", ".py"})
EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
    }
)
SENSITIVE_NAMES = frozenset(
    {
        "credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
    }
)
SENSITIVE_EXTENSIONS = frozenset({".key", ".kdbx", ".p12", ".pfx", ".pem"})


@dataclass(frozen=True)
class FilePolicy:
    extensions: frozenset[str] = DEFAULT_EXTENSIONS
    max_file_bytes: int = 20 * 1_048_576

    def directory_reason(self, path: Path) -> str | None:
        if path.is_symlink():
            return "symlink"
        if path.name.startswith(".") or path.name in EXCLUDED_DIRECTORIES:
            return "excluded_directory"
        return None

    def file_reason(self, path: Path) -> str | None:
        name = path.name.lower()
        if path.is_symlink():
            return "symlink"
        if name == ".env" or name.startswith(".env."):
            return "sensitive_file"
        if name in SENSITIVE_NAMES or path.suffix.lower() in SENSITIVE_EXTENSIONS:
            return "sensitive_file"
        if name.startswith("."):
            return "hidden_file"
        if path.suffix.lower() not in self.extensions:
            return "unsupported_type"
        try:
            if path.stat().st_size > self.max_file_bytes:
                return "too_large"
        except OSError:
            return "unreadable"
        return None


def read_allowed_text(path: Path, policy: FilePolicy) -> tuple[str | None, str | None]:
    reason = policy.file_reason(path)
    if reason:
        return None, reason
    try:
        raw = path.read_bytes()
    except OSError:
        return None, "unreadable"
    if b"\x00" in raw:
        return None, "binary"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "invalid_utf8"
