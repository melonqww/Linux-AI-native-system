import hashlib
import io
import json
import shutil
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from ai_native_provider_ollama import OllamaProviderInstaller, ProviderDecisionStore


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Response:
    def __init__(self, body, url):
        self.body = io.BytesIO(body)
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.body.read(size)

    def geturl(self):
        return self.url


class Clock:
    def __init__(self):
        self.value = datetime(2026, 8, 21, 12, tzinfo=UTC)

    def __call__(self):
        return self.value


class FakeProcess:
    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class LocalOllama:
    def __init__(self, *, ready=False):
        self.ready = ready
        self.starts = []
        self.processes = []

    def open(self, request, timeout):
        if not request.full_url.endswith("/api/version") or not self.ready:
            raise OSError("server unavailable")
        return Response(json.dumps({"version": "0.12.6"}).encode(), request.full_url)

    def popen(self, arguments, **_kwargs):
        self.starts.append(arguments)
        self.ready = True
        process = FakeProcess()
        self.processes.append(process)
        return process


class ProviderInstallerTests(unittest.TestCase):
    def setUp(self):
        self.root = PROJECT_ROOT / "tmp" / "provider-installer-tests" / str(uuid4())
        self.root.mkdir(parents=True)
        self.clock = Clock()
        self.store = ProviderDecisionStore(self.root / "state.sqlite3")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def installer(self, **overrides):
        options = {
            "platform_name": "linux",
            "machine": "x86_64",
            "which_fn": lambda _name: None,
            "now_fn": self.clock,
            "local_open_fn": lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("server unavailable")
            ),
        }
        options.update(overrides)
        return OllamaProviderInstaller(self.root / "provider", self.store, **options)

    def test_missing_provider_requires_consent_without_network(self):
        installer = self.installer(open_fn=lambda *_args, **_kwargs: self.fail("network"))

        status = installer.status()

        self.assertEqual(status.state, "consent_required")
        self.assertTrue(status.prompt_required)
        self.assertEqual(status.decision, "unset")

    def test_later_and_never_persist(self):
        installer = self.installer()
        self.assertEqual(installer.respond("later").state, "deferred")
        self.assertEqual(self.installer().status().state, "deferred")
        self.clock.value += timedelta(hours=24, seconds=1)
        self.assertEqual(self.installer().status().state, "consent_required")
        self.assertEqual(self.installer().respond("never").state, "declined")
        self.assertEqual(self.installer().status().state, "declined")

    def test_verified_release_installs_in_background(self):
        archive = b"verified archive payload"
        digest = hashlib.sha256(archive).hexdigest()
        asset_url = "https://github.com/ollama/ollama/releases/download/v1.2.3/ollama-linux-amd64.tar.zst"
        metadata = json.dumps(
            {
                "tag_name": "v1.2.3",
                "assets": [{
                    "name": "ollama-linux-amd64.tar.zst",
                    "browser_download_url": asset_url,
                    "size": len(archive),
                    "digest": f"sha256:{digest}",
                }],
            }
        ).encode()

        def open_fn(request, timeout):
            if request.full_url.endswith("/releases/latest"):
                return Response(metadata, request.full_url)
            return Response(archive, asset_url)

        def extract_fn(_archive, destination):
            executable = destination / "bin" / "ollama"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"binary")

        provider_root = self.root / "provider"
        staging = self.root / "test-staging"
        staging.mkdir()

        def publish_fn(extracted, _tag):
            destination = provider_root / "current"
            shutil.copytree(extracted, destination)
            (destination / "bin" / "ollama").chmod(0o755)

        local = LocalOllama()
        installer = self.installer(
            open_fn=open_fn,
            local_open_fn=local.open,
            extract_fn=extract_fn,
            publish_fn=publish_fn,
            staging_fn=lambda _downloads: staging,
            popen_fn=local.popen,
            sleep_fn=lambda _seconds: None,
            run_fn=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0, stdout="ollama version 1.2.3", stderr=""
            ),
        )
        try:
            first = installer.respond("install")
            for _ in range(200):
                status = installer.status()
                if status.state == "ready":
                    break
                threading.Event().wait(0.01)
            else:
                self.fail(f"installer did not become ready: {status}")
        finally:
            installer.stop()

        self.assertIn(first.state, {"downloading", "installing", "ready"})
        self.assertTrue(status.installed)
        self.assertTrue(status.managed)
        self.assertEqual(status.version, "0.12.6")
        self.assertEqual(len(local.starts), 1)

    def test_managed_provider_is_the_only_component_that_starts_server(self):
        executable = self.root / "provider/current/bin/ollama"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"binary")
        executable.chmod(0o755)
        local = LocalOllama()
        installer = self.installer(
            local_open_fn=local.open,
            popen_fn=local.popen,
            sleep_fn=lambda _seconds: None,
            run_fn=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0, stdout="ollama version 0.12.6", stderr=""
            ),
        )
        try:
            installer.status()
            for _ in range(100):
                status = installer.status()
                if status.state == "ready":
                    break
                threading.Event().wait(0.01)
            else:
                self.fail(f"managed server did not become ready: {status}")
            installer.status()
        finally:
            installer.stop()

        self.assertEqual(len(local.starts), 1)
        self.assertEqual(local.starts[0][1], "serve")

    def test_external_binary_without_server_is_not_started_as_current_user(self):
        executable = self.root / "system/bin/ollama"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"binary")
        executable.chmod(0o755)
        installer = self.installer(
            which_fn=lambda _name: str(executable),
            local_open_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("server unavailable")
            ),
            popen_fn=lambda *_args, **_kwargs: self.fail("external binary was started"),
            run_fn=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0, stdout="ollama version 0.12.6", stderr=""
            ),
        )

        status = installer.status()

        self.assertEqual(status.state, "error")
        self.assertTrue(status.installed)
        self.assertFalse(status.managed)
        self.assertEqual(status.reason, "external_server_unavailable")

    def test_rejects_missing_digest_and_unsafe_download_host(self):
        installer = self.installer()
        self.assertFalse(installer._safe_url("https://example.com/ollama.tar.zst"))
        self.assertFalse(installer._safe_url("http://github.com/file"))
        with self.assertRaises(ValueError):
            installer.respond("maybe")

        metadata = json.dumps({
            "tag_name": "v1.2.3",
            "assets": [{
                "name": "ollama-linux-amd64.tar.zst",
                "browser_download_url": "https://github.com/ollama/ollama/releases/file",
                "size": 100,
                "digest": None,
            }],
        }).encode()
        installer = self.installer(
            open_fn=lambda request, timeout: Response(metadata, request.full_url)
        )
        with self.assertRaisesRegex(RuntimeError, "release_digest_missing"):
            installer._release("amd64")

    def test_non_linux_platform_is_unsupported(self):
        status = self.installer(platform_name="windows").status()
        self.assertEqual(status.state, "unsupported")
        self.assertFalse(status.prompt_required)


if __name__ == "__main__":
    unittest.main()
