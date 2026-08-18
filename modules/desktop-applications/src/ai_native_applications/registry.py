import configparser
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ApplicationInfo:
    name: str
    desktop_file: str
    executable: str
    description: str | None


class ApplicationRegistry:
    def __init__(self, roots: tuple[Path, ...] | None = None) -> None:
        if roots is None:
            data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
            roots = (data_home / "applications", Path("/usr/local/share/applications"), Path("/usr/share/applications"))
        self.roots = roots

    def find(self, query: str, limit: int = 20) -> list[ApplicationInfo]:
        query = query.strip().casefold()
        if not query:
            raise ValueError("application query must not be empty")
        found: list[ApplicationInfo] = []
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in root.glob("*.desktop"):
                info = self._read(path)
                if info and query in f"{info.name} {Path(info.executable).name}".casefold():
                    found.append(info)
        return sorted(found, key=lambda item: item.name.casefold())[:limit]

    @staticmethod
    def _read(path: Path) -> ApplicationInfo | None:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read(path, encoding="utf-8")
            entry = parser["Desktop Entry"]
            if entry.get("Type", "Application") != "Application" or entry.getboolean("NoDisplay", fallback=False):
                return None
            name, executable = entry.get("Name", "").strip(), entry.get("Exec", "").strip()
            if not name or not executable:
                return None
            return ApplicationInfo(name, str(path), executable, entry.get("Comment"))
        except (OSError, configparser.Error):
            return None
