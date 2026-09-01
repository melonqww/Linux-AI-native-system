"""Disposable virtual desktop whose physical paths never escape the lab directory."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


class VirtualPathError(ValueError):
    pass


@dataclass(frozen=True)
class VirtualVolume:
    volume_id: str
    name: str
    physical_root: Path
    virtual_root: str
    permission: str
    is_system: bool


class VirtualComputer:
    MARKER = ".ai-native-scenario-lab"

    def __init__(self, root: Path, *, lab_root: Path) -> None:
        self.root = root.resolve()
        self.lab_root = lab_root.resolve()
        if not self.root.is_relative_to(self.lab_root):
            raise VirtualPathError("virtual computer must live inside the scenario lab")
        if (
            self.root == self.lab_root
            or len(self.root.relative_to(self.lab_root).parts) < 2
        ):
            raise VirtualPathError("virtual computer root is too broad")
        self.volumes: tuple[VirtualVolume, ...] = ()

    def provision(self, fixture_path: Path) -> None:
        self._safe_reset()
        self.root.mkdir(parents=True)
        (self.root / self.MARKER).write_text(
            "managed virtual computer\n", encoding="utf-8"
        )
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"volumes", "files"}:
            raise ValueError("virtual computer fixture fields are invalid")
        volumes: list[VirtualVolume] = []
        seen: set[str] = set()
        virtual_roots: set[str] = set()
        for item in payload["volumes"]:
            if not isinstance(item, dict) or set(item) != {
                "id",
                "name",
                "virtual_root",
                "permission",
                "system",
            }:
                raise ValueError("virtual volume fields are invalid")
            volume_id = self._token(item["id"], "volume id")
            if volume_id in seen:
                raise ValueError("duplicate virtual volume")
            permission = item["permission"]
            if permission not in {"none", "metadata", "content"}:
                raise ValueError("virtual volume permission is invalid")
            if not isinstance(item["system"], bool):
                raise ValueError("virtual volume system flag must be boolean")
            virtual_root = self._virtual_root(item["virtual_root"])
            if virtual_root in virtual_roots:
                raise ValueError("duplicate virtual mount point")
            physical = self.root / "volumes" / volume_id
            physical.mkdir(parents=True)
            volumes.append(
                VirtualVolume(
                    volume_id,
                    self._token(item["name"], "volume name", maximum=120),
                    physical,
                    virtual_root,
                    permission,
                    item["system"],
                )
            )
            seen.add(volume_id)
            virtual_roots.add(virtual_root)
        self.volumes = tuple(volumes)
        if sum(item.is_system for item in self.volumes) != 1:
            raise ValueError("fixture must declare exactly one system volume")
        by_id = {item.volume_id: item for item in self.volumes}
        for item in payload["files"]:
            self._write_fixture(item, by_id)
        for role in ("Desktop", "Documents", "Downloads", "Pictures"):
            (self.home / role).mkdir(parents=True, exist_ok=True)

    @property
    def home(self) -> Path:
        system = next((item for item in self.volumes if item.is_system), None)
        if system is None:
            raise RuntimeError("fixture has no system volume")
        home = system.physical_root / "home" / "test-user"
        home.mkdir(parents=True, exist_ok=True)
        return home

    def virtual_path(self, physical: Path) -> str:
        resolved = physical.resolve()
        for volume in self.volumes:
            if resolved.is_relative_to(volume.physical_root):
                relative = resolved.relative_to(volume.physical_root).as_posix()
                return str(PurePosixPath(volume.virtual_root) / relative)
        raise VirtualPathError("physical path is outside virtual volumes")

    def physical_path(self, virtual: str) -> Path:
        candidate = PurePosixPath(virtual)
        if not candidate.is_absolute() or ".." in candidate.parts:
            raise VirtualPathError("virtual path must be absolute and normalized")
        # More specific mount points must win over the system root. Otherwise
        # /mnt/archive could incorrectly resolve inside the system volume.
        for volume in sorted(
            self.volumes,
            key=lambda item: len(PurePosixPath(item.virtual_root).parts),
            reverse=True,
        ):
            root = PurePosixPath(volume.virtual_root)
            try:
                relative = candidate.relative_to(root)
            except ValueError:
                continue
            physical = (volume.physical_root / Path(*relative.parts)).resolve()
            if not physical.is_relative_to(volume.physical_root):
                raise VirtualPathError("virtual path escaped its volume")
            return physical
        raise VirtualPathError("virtual path is outside enrolled volumes")

    def snapshot(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                self.virtual_path(path)
                for path in self.root.glob("volumes/**/*")
                if path.is_file() and path.name != self.MARKER
            )
        )

    def close(self) -> None:
        # Runs remain on disk for report inspection. The next provision performs
        # a marker-gated reset, so no broad or user-owned path can be deleted.
        return

    def _safe_reset(self) -> None:
        if not self.root.exists():
            return
        marker = self.root / self.MARKER
        if not marker.is_file():
            raise VirtualPathError("refusing to reset an unmarked directory")
        shutil.rmtree(self.root)

    def _write_fixture(self, item: object, volumes: dict[str, VirtualVolume]) -> None:
        if not isinstance(item, dict) or set(item) - {
            "volume",
            "path",
            "kind",
            "content",
            "size_bytes",
        }:
            raise ValueError("virtual file fields are invalid")
        volume_id = self._token(item.get("volume"), "file volume")
        volume = volumes.get(volume_id)
        if volume is None:
            raise ValueError("virtual file references an unknown volume")
        relative = PurePosixPath(
            self._token(item.get("path"), "file path", maximum=500)
        )
        if relative.is_absolute() or ".." in relative.parts:
            raise VirtualPathError("fixture file path escaped its volume")
        destination = (volume.physical_root / Path(*relative.parts)).resolve()
        if not destination.is_relative_to(volume.physical_root):
            raise VirtualPathError("fixture destination escaped its volume")
        destination.parent.mkdir(parents=True, exist_ok=True)
        kind = item.get("kind", "text")
        content = item.get("content", "")
        if not isinstance(content, str):
            raise ValueError("virtual file content must be text")
        if kind == "text":
            destination.write_text(content, encoding="utf-8")
        elif kind == "broken_pdf":
            destination.write_bytes(b"%PDF-1.7\ntruncated")
        elif kind == "pdf":
            self._write_pdf(destination, content)
        elif kind == "large_text":
            size = item.get("size_bytes", 2_097_152)
            if not isinstance(size, int) or not 1_048_577 <= size <= 8_388_608:
                raise ValueError("large fixture size is invalid")
            prefix = (content + "\n").encode("utf-8")
            destination.write_bytes(prefix + b"x" * max(0, size - len(prefix)))
        else:
            raise ValueError("unknown virtual file kind")

    @staticmethod
    def _write_pdf(path: Path, text: str) -> None:
        safe_text = (
            text.encode("ascii", "replace")
            .decode("ascii")
            .replace("(", "[")
            .replace(")", "]")
        )
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        reference = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): reference})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 14 Tf 72 720 Td ({safe_text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        with path.open("wb") as output:
            writer.write(output)

    @staticmethod
    def _token(value: object, label: str, *, maximum: int = 80) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{label} must be text")
        value = value.strip()
        if not value or len(value) > maximum or any(ord(char) < 32 for char in value):
            raise ValueError(f"{label} is invalid")
        return value

    @classmethod
    def _virtual_root(cls, value: object) -> str:
        root = cls._token(value, "virtual root", maximum=200)
        path = PurePosixPath(root)
        if not path.is_absolute() or ".." in path.parts:
            raise VirtualPathError("virtual volume root is unsafe")
        return str(path)
