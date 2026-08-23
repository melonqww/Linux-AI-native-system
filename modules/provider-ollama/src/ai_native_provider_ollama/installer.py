"""Verified, user-local Ollama provider lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from .contracts import ProviderStatus
from .store import DECISIONS, ProviderDecisionStore


_RELEASE_API = "https://api.github.com/repos/ollama/ollama/releases/latest"
_MAX_METADATA_BYTES = 2 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
_MAX_EXTRACTED_BYTES = 12 * 1024 * 1024 * 1024
_MAX_ARCHIVE_ENTRIES = 100_000
_MAX_LOCAL_RESPONSE_BYTES = 64 * 1024
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")
_TAG = re.compile(r"^v?[0-9][A-Za-z0-9._-]{0,63}$")
_DOWNLOAD_HOSTS = frozenset(
    {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
)


class OllamaProviderInstaller:
    def __init__(
        self,
        root: Path,
        decisions: ProviderDecisionStore,
        *,
        platform_name: str | None = None,
        machine: str | None = None,
        base_url: str = "http://127.0.0.1:11434",
        open_fn: Callable[..., object] | None = None,
        local_open_fn: Callable[..., object] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        which_fn: Callable[[str], str | None] | None = None,
        run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        popen_fn: Callable[..., subprocess.Popen[bytes]] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        extract_fn: Callable[[Path, Path], None] | None = None,
        publish_fn: Callable[[Path, str], None] | None = None,
        staging_fn: Callable[[Path], Path] | None = None,
    ) -> None:
        self.root = Path(root).expanduser().absolute()
        self.decisions = decisions
        self.platform = platform_name or platform.system().lower()
        self.machine = (machine or platform.machine()).lower()
        self.base_url, self.ollama_host = self._base_url(base_url)
        self._open = open_fn or build_opener(ProxyHandler({})).open
        self._local_open = local_open_fn or build_opener(ProxyHandler({})).open
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._which = which_fn or shutil.which
        self._run = run_fn or subprocess.run
        self._popen = popen_fn or subprocess.Popen
        self._sleep = sleep_fn or time.sleep
        self._extract = extract_fn or self._extract_archive
        self._publish = publish_fn or self._publish_installation
        self._staging = staging_fn or self._create_staging
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._server_thread: threading.Thread | None = None
        self._server_start_lock = threading.Lock()
        self._owned_server: subprocess.Popen[bytes] | None = None
        self._state = "checking"
        self._progress: int | None = None
        self._completed = 0
        self._total: int | None = None
        self._reason: str | None = None
        self._versions: dict[str, str | None] = {}

    @property
    def managed_executable(self) -> Path:
        return self.root / "current" / "bin" / "ollama"

    def status(self) -> ProviderStatus:
        server_version = self._server_version()
        executable, managed = self._installed_executable()
        if server_version is not None:
            self._set_state("ready")
            return self._status("ready", True, managed, server_version, None)
        if executable is not None:
            version = self._version(executable)
            if managed:
                with self._lock:
                    installing = (
                        self._thread is not None
                        and self._thread.is_alive()
                        and self._state in {"downloading", "installing"}
                    )
                if not installing:
                    self._ensure_server(executable)
                with self._lock:
                    state = self._state
                    reason = self._reason
                return self._status(state, True, True, version, reason)
            return self._status(
                "error", True, False, version, "external_server_unavailable"
            )
        unsupported = self._architecture() is None
        if unsupported:
            return self._status("unsupported", False, False, None, "platform_unsupported")
        decision, dismissed = self.decisions.get()
        if decision == "install":
            self._ensure_install()
            with self._lock:
                return self._status(
                    self._state, False, False, None, self._reason,
                    self._progress, self._completed, self._total
                )
        now = self._now()
        if decision == "later" and dismissed is not None and dismissed > now:
            return self._status("deferred", False, False, None, "user_deferred")
        if decision == "never":
            return self._status("declined", False, False, None, "user_declined")
        return self._status("consent_required", False, False, None, "user_decision_required")

    def respond(self, decision: str) -> ProviderStatus:
        if decision not in DECISIONS:
            raise ValueError("decision must be install, later or never")
        self.decisions.set(decision, now=self._now())
        if decision == "install":
            with self._lock:
                if self._state == "error":
                    self._state = "checking"
                    self._reason = None
        else:
            self._stop.set()
        return self.status()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        server_thread = self._server_thread
        if server_thread is not None:
            server_thread.join(timeout=2)
        process = self._owned_server
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        self._owned_server = None

    def _ensure_install(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._state == "error":
                return
            self._stop.clear()
            self._state = "downloading"
            self._reason = None
            self._thread = threading.Thread(
                target=self._install_worker, name="ollama-provider-install", daemon=True
            )
            self._thread.start()

    def _install_worker(self) -> None:
        try:
            architecture = self._architecture()
            if architecture is None:
                self._set_state("unsupported", reason="platform_unsupported")
                return
            release = self._release(architecture)
            self.root.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(self.root).free < int(release["size"]) * 3:
                raise RuntimeError("insufficient_disk_space")
            downloads = self.root / "downloads"
            downloads.mkdir(mode=0o700, parents=True, exist_ok=True)
            staging = self._staging(downloads)
            try:
                archive = staging / release["name"]
                self._download(release, archive)
                if self._stop.is_set():
                    self._set_state("error", reason="install_interrupted")
                    return
                self._set_state("installing")
                extracted = staging / "root"
                extracted.mkdir()
                self._extract(archive, extracted)
                executable = extracted / "bin" / "ollama"
                if not executable.is_file():
                    raise RuntimeError("archive_missing_executable")
                executable.chmod(0o755)
                self._publish(extracted, str(release["tag"]))
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            executable, managed = self._installed_executable()
            if executable is None or not managed:
                raise RuntimeError("provider_not_available_after_install")
            if not self._start_managed_server(executable):
                if self._stop.is_set():
                    raise RuntimeError("provider_start_interrupted")
                raise RuntimeError("provider_server_start_failed")
        except Exception as error:
            public_reasons = {
                "archive_digest_mismatch",
                "archive_missing_executable",
                "archive_has_too_many_entries",
                "archive_too_large",
                "download_incomplete",
                "extracted_content_too_large",
                "invalid_release_metadata",
                "insufficient_disk_space",
                "platform_unsupported",
                "provider_not_available_after_install",
                "provider_server_start_failed",
                "provider_start_interrupted",
                "release_asset_missing",
                "release_digest_missing",
                "unsafe_archive_link",
                "unsafe_archive_path",
                "unsafe_download_redirect",
                "unsupported_archive_entry",
                "zstandard_unavailable",
            }
            reason = str(error)
            self._set_state(
                "error", reason=reason if reason in public_reasons else "install_failed"
            )

    def _release(self, architecture: str) -> dict[str, object]:
        request = Request(
            _RELEASE_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "ai-native-linux"},
        )
        with self._open(request, timeout=15) as response:
            body = response.read(_MAX_METADATA_BYTES + 1)
        if len(body) > _MAX_METADATA_BYTES:
            raise RuntimeError("release_metadata_too_large")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, Mapping) or not isinstance(payload.get("assets"), list):
            raise RuntimeError("invalid_release_metadata")
        tag = payload.get("tag_name")
        if not isinstance(tag, str) or _TAG.fullmatch(tag) is None:
            raise RuntimeError("invalid_release_tag")
        name = f"ollama-linux-{architecture}.tar.zst"
        for asset in payload["assets"]:
            if not isinstance(asset, Mapping) or asset.get("name") != name:
                continue
            url, size, digest = asset.get("browser_download_url"), asset.get("size"), asset.get("digest")
            if not isinstance(url, str) or not self._safe_url(url):
                raise RuntimeError("invalid_release_url")
            if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= _MAX_ARCHIVE_BYTES:
                raise RuntimeError("invalid_release_size")
            if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
                raise RuntimeError("release_digest_missing")
            return {"tag": tag, "name": name, "url": url, "size": size, "digest": digest[7:]}
        raise RuntimeError("release_asset_missing")

    def _download(self, release: Mapping[str, object], destination: Path) -> None:
        request = Request(str(release["url"]), headers={"User-Agent": "ai-native-linux"})
        digest = hashlib.sha256()
        completed = 0
        total = int(release["size"])
        with self._open(request, timeout=30) as response, destination.open("xb") as output:
            final_url = response.geturl() if hasattr(response, "geturl") else str(release["url"])
            if not self._safe_url(final_url):
                raise RuntimeError("unsafe_download_redirect")
            while not self._stop.is_set():
                block = response.read(1024 * 1024)
                if not block:
                    break
                completed += len(block)
                if completed > total or completed > _MAX_ARCHIVE_BYTES:
                    raise RuntimeError("archive_too_large")
                output.write(block)
                digest.update(block)
                self._set_state("downloading", completed=completed, total=total)
        if self._stop.is_set() or completed != total:
            raise RuntimeError("download_incomplete")
        if digest.hexdigest() != release["digest"]:
            raise RuntimeError("archive_digest_mismatch")

    @staticmethod
    def _extract_archive(archive: Path, destination: Path) -> None:
        try:
            import zstandard
        except ImportError as error:
            raise RuntimeError("zstandard_unavailable") from error
        extracted = 0
        with archive.open("rb") as source:
            with zstandard.ZstdDecompressor().stream_reader(source) as stream:
                with tarfile.open(fileobj=stream, mode="r|") as bundle:
                    for index, member in enumerate(bundle):
                        if index >= _MAX_ARCHIVE_ENTRIES:
                            raise RuntimeError("archive_has_too_many_entries")
                        relative = PurePosixPath(member.name)
                        if (
                            relative.is_absolute()
                            or ".." in relative.parts
                            or not relative.parts
                            or relative.parts[0] not in {"bin", "lib"}
                        ):
                            raise RuntimeError("unsafe_archive_path")
                        target = destination.joinpath(*relative.parts)
                        if member.isdir():
                            target.mkdir(mode=0o755, parents=True, exist_ok=True)
                        elif member.isfile():
                            extracted += member.size
                            if extracted > _MAX_EXTRACTED_BYTES:
                                raise RuntimeError("extracted_content_too_large")
                            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                            source_file = bundle.extractfile(member)
                            if source_file is None:
                                raise RuntimeError("archive_file_unavailable")
                            with target.open("xb") as output:
                                shutil.copyfileobj(source_file, output, length=1024 * 1024)
                            target.chmod(member.mode & 0o755)
                        elif member.issym():
                            link = PurePosixPath(member.linkname)
                            if link.is_absolute() or ".." in link.parts:
                                raise RuntimeError("unsafe_archive_link")
                            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                            target.symlink_to(member.linkname)
                        else:
                            raise RuntimeError("unsupported_archive_entry")

    def _publish_installation(self, extracted: Path, tag: str) -> None:
        releases = self.root / "releases"
        releases.mkdir(exist_ok=True)
        destination = releases / tag
        if not destination.exists():
            extracted.replace(destination)
        link = self.root / f".current-{os.getpid()}-{threading.get_ident()}"
        link.symlink_to(Path("releases") / tag, target_is_directory=True)
        os.replace(link, self.root / "current")

    @staticmethod
    def _create_staging(downloads: Path) -> Path:
        staging = downloads / f"install-{uuid4()}"
        staging.mkdir(mode=0o700)
        return staging

    def _installed_executable(self) -> tuple[Path | None, bool]:
        system = self._which("ollama")
        if system:
            try:
                path = Path(system).resolve(strict=True)
            except OSError:
                path = None
            if path is not None and path.is_file() and os.access(path, os.X_OK):
                return path, False
        path = self.managed_executable
        if path.is_file() and os.access(path, os.X_OK):
            return path, True
        return None, False

    def _ensure_server(self, executable: Path) -> None:
        with self._lock:
            if self._server_thread is not None and self._server_thread.is_alive():
                return
            self._stop.clear()
            self._state = "starting"
            self._reason = None
            self._server_thread = threading.Thread(
                target=self._server_worker,
                args=(executable,),
                name="ollama-provider-server",
                daemon=True,
            )
            self._server_thread.start()

    def _server_worker(self, executable: Path) -> None:
        if not self._start_managed_server(executable) and not self._stop.is_set():
            self._set_state("error", reason="provider_server_start_failed")

    def _start_managed_server(self, executable: Path) -> bool:
        with self._server_start_lock:
            if self._server_version() is not None:
                self._set_state("ready")
                return True
            environment = os.environ.copy()
            environment["OLLAMA_HOST"] = self.ollama_host
            library = self.root / "current" / "lib"
            if library.is_dir():
                existing = environment.get("LD_LIBRARY_PATH", "")
                environment["LD_LIBRARY_PATH"] = os.pathsep.join(
                    value for value in (str(library), existing) if value
                )
            try:
                self._owned_server = self._popen(
                    [str(executable), "serve"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=environment,
                    cwd=Path.home(),
                )
            except OSError:
                return False
            for _ in range(40):
                if self._stop.is_set():
                    return False
                version = self._server_version()
                if version is not None:
                    self._set_state("ready")
                    return True
                if self._owned_server.poll() is not None:
                    break
                self._sleep(0.25)
            return False

    def _server_version(self) -> str | None:
        request = Request(
            f"{self.base_url}/api/version",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with self._local_open(request, timeout=2) as response:
                body = response.read(_MAX_LOCAL_RESPONSE_BYTES + 1)
            if len(body) > _MAX_LOCAL_RESPONSE_BYTES:
                return None
            payload = json.loads(body.decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None
        version = payload.get("version")
        return version[:120] if isinstance(version, str) and version else None

    def _version(self, executable: Path) -> str | None:
        key = str(executable)
        with self._lock:
            if key in self._versions:
                return self._versions[key]
        try:
            result = self._run(
                [str(executable), "--version"], stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=5, check=False,
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError):
            version = None
        else:
            text = (result.stdout or result.stderr or "").strip()
            version = text[:120] or None
        with self._lock:
            self._versions[key] = version
        return version

    def _architecture(self) -> str | None:
        if self.platform != "linux":
            return None
        return {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(self.machine)

    def _status(
        self, state: str, installed: bool, managed: bool, version: str | None,
        reason: str | None, progress: int | None = None, completed: int = 0,
        total: int | None = None,
    ) -> ProviderStatus:
        decision, dismissed = self.decisions.get()
        now = self._now()
        prompt = (
            not installed and state == "consent_required" and decision != "never"
            and not (decision == "later" and dismissed is not None and dismissed > now)
        )
        return ProviderStatus(
            1, "ollama", "Ollama", state, installed, managed, version,
            decision or "unset", prompt, progress, completed, total, reason,
        )

    def _set_state(
        self, state: str, *, reason: str | None = None,
        completed: int = 0, total: int | None = None,
    ) -> None:
        with self._lock:
            self._state, self._reason = state, reason
            self._completed, self._total = completed, total
            self._progress = None if not total else min(100, int(completed * 100 / total))

    def _now(self) -> datetime:
        value = self._now_fn()
        if value.tzinfo is None:
            raise ValueError("provider clock must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _base_url(value: str) -> tuple[str, str]:
        if not isinstance(value, str):
            raise TypeError("base_url must be a string")
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme != "http"
            or parsed.hostname not in _LOOPBACK
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Ollama base URL must be a loopback HTTP origin")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("Ollama base URL has an invalid port") from error
        if port is None:
            raise ValueError("Ollama base URL must include a port")
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        return f"http://{host}:{port}", f"{host}:{port}"

    @staticmethod
    def _safe_url(value: str) -> bool:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https" and not parsed.username and not parsed.password
            and parsed.hostname in _DOWNLOAD_HOSTS and not parsed.fragment
        )
