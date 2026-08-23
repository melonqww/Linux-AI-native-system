"""Integration tests for volumes, metadata catalog and virtual collections."""

import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from ai_native_storage import (
    CollectionKind,
    FileCatalog,
    FileQuery,
    PermissionLevel,
    StorageEnrollment,
    VirtualCollectionStore,
    VolumeRegistry,
)
from ai_native_storage.contracts import DiscoveredVolume
from ai_native_storage.discovery import LinuxVolumeDiscovery


class FakeDiscovery:
    def __init__(self, volumes: list[DiscoveredVolume]) -> None:
        self.volumes = volumes

    def discover(self) -> list[DiscoveredVolume]:
        return list(self.volumes)


class StorageTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary_root = Path(__file__).resolve().parents[3] / "tmp" / "storage-catalog-tests"
        self.base = temporary_root / str(uuid4())
        self.base.mkdir(parents=True)
        self.system_root = self.base / "system-volume"
        self.external_root = self.base / "external-volume"
        self.system_root.mkdir()
        self.external_root.mkdir()
        self.database = self.base / "catalog.sqlite3"
        self.system_volume = DiscoveredVolume(
            volume_id="system-volume",
            name="System",
            mount_point=str(self.system_root),
            device="test-system",
            fs_type="testfs",
            is_system=True,
            is_removable=False,
            is_network=False,
        )
        self.external_volume = DiscoveredVolume(
            volume_id="external-volume",
            name="Study",
            mount_point=str(self.external_root),
            device="test-external",
            fs_type="testfs",
            is_system=False,
            is_removable=True,
            is_network=False,
        )
        self.discovery = FakeDiscovery([self.system_volume, self.external_volume])
        self.registry = VolumeRegistry(self.database, discovery=self.discovery)
        self.registry.refresh()

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)


class VolumeRegistryTests(StorageTestCase):
    def test_system_volume_is_allowed_and_external_volume_requires_permission(self) -> None:
        system = self.registry.get_volume("system-volume")
        external = self.registry.get_volume("external-volume")

        self.assertEqual(system.permission, PermissionLevel.CONTENT)
        self.assertEqual(external.permission, PermissionLevel.NONE)

    def test_permission_survives_refresh_and_missing_volume_becomes_unavailable(self) -> None:
        self.registry.set_permission("external-volume", PermissionLevel.CONTENT)
        self.registry.refresh()
        self.assertEqual(
            self.registry.get_volume("external-volume").permission,
            PermissionLevel.CONTENT,
        )

        self.discovery.volumes = [self.system_volume]
        self.registry.refresh()

        external = self.registry.get_volume("external-volume")
        self.assertFalse(external.is_available)
        self.assertEqual(external.permission, PermissionLevel.CONTENT)

    def test_storage_enrollment_requires_consent_only_for_additional_disks(self) -> None:
        enrollment = StorageEnrollment(self.registry)
        by_id = {item.volume_id: item for item in enrollment.list()}

        self.assertFalse(by_id["system-volume"].permission_required)
        self.assertTrue(by_id["external-volume"].permission_required)
        enrolled = enrollment.set_permission("external-volume", PermissionLevel.METADATA)
        self.assertEqual(enrolled.permission, PermissionLevel.METADATA)
        self.assertFalse(enrolled.permission_required)
        disabled = enrollment.set_permission("system-volume", PermissionLevel.NONE)
        self.assertEqual(disabled.permission, PermissionLevel.NONE)


class FileCatalogTests(StorageTestCase):
    def setUp(self) -> None:
        super().setUp()
        (self.system_root / "algebra.pdf").write_bytes(b"fake pdf")
        (self.system_root / "notes.txt").write_text("notes", encoding="utf-8")
        (self.system_root / ".env").write_text("TOKEN=secret", encoding="utf-8")
        project = self.system_root / "calculator"
        project.mkdir()
        (project / "pyproject.toml").write_text("[project]", encoding="utf-8")
        (project / "main.py").write_text("print(2 + 2)", encoding="utf-8")
        self.catalog = FileCatalog(self.database)

    def test_scans_registered_volume_and_searches_metadata(self) -> None:
        report = self.catalog.scan_volume("system-volume")
        pdfs = self.catalog.search(FileQuery(extensions=("pdf",)))
        projects = self.catalog.search(FileQuery(roles=("python_project",)))

        self.assertEqual(report.discovered, 6)
        self.assertEqual([entry.name for entry in pdfs], ["algebra.pdf"])
        self.assertEqual([entry.name for entry in projects], ["calculator"])

    def test_sensitive_metadata_is_hidden_from_normal_search(self) -> None:
        self.catalog.scan_volume("system-volume")

        normal = self.catalog.search(FileQuery(name_contains=(".env",)))
        privileged = self.catalog.search(
            FileQuery(name_contains=(".env",)),
            allow_sensitive_metadata=True,
        )

        self.assertEqual(normal, [])
        self.assertEqual(len(privileged), 1)
        self.assertTrue(privileged[0].sensitive)

    def test_deleted_entries_are_removed_on_rescan(self) -> None:
        document = self.system_root / "algebra.pdf"
        self.catalog.scan_volume("system-volume")
        document.unlink()

        report = self.catalog.scan_volume("system-volume")

        self.assertEqual(report.removed, 1)
        self.assertEqual(self.catalog.search(FileQuery(extensions=("pdf",))), [])

    def test_additional_volume_cannot_be_scanned_without_permission(self) -> None:
        with self.assertRaises(PermissionError):
            self.catalog.scan_volume("external-volume")

        self.registry.set_permission("external-volume", PermissionLevel.METADATA)
        report = self.catalog.scan_volume("external-volume")

        self.assertEqual(report.discovered, 0)

    def test_revoked_volume_disappears_from_search_without_losing_catalog(self) -> None:
        (self.external_root / "external.pdf").write_bytes(b"pdf")
        self.registry.set_permission("external-volume", PermissionLevel.METADATA)
        self.catalog.scan_volume("external-volume")
        self.assertEqual(
            len(self.catalog.search(FileQuery(volume_ids=("external-volume",)))),
            1,
        )

        self.registry.set_permission("external-volume", PermissionLevel.NONE)

        self.assertEqual(
            self.catalog.search(FileQuery(volume_ids=("external-volume",))),
            [],
        )
        self.assertEqual(self.catalog.status()["by_volume"]["external-volume"], 1)


class VirtualCollectionTests(StorageTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.first_pdf = self.system_root / "algebra.pdf"
        self.first_pdf.write_bytes(b"fake pdf")
        self.catalog = FileCatalog(self.database)
        self.catalog.scan_volume("system-volume")
        self.collections = VirtualCollectionStore(self.database)

    def test_smart_collection_reflects_new_catalog_results(self) -> None:
        collection = self.collections.create_smart(
            "Математические PDF",
            FileQuery(extensions=("pdf",)),
        )
        self.assertEqual(collection.kind, CollectionKind.SMART)
        self.assertEqual(len(self.collections.resolve(collection.collection_id)), 1)

        (self.system_root / "geometry.pdf").write_bytes(b"another pdf")
        self.catalog.scan_volume("system-volume")

        self.assertEqual(len(self.collections.resolve(collection.collection_id)), 2)

    def test_snapshot_keeps_reference_and_marks_deleted_file_unavailable(self) -> None:
        entries = self.catalog.search(FileQuery(extensions=("pdf",)))
        collection = self.collections.create_snapshot("Снимок PDF", entries)
        self.first_pdf.unlink()
        self.catalog.scan_volume("system-volume")

        items = self.collections.resolve(collection.collection_id)

        self.assertEqual(collection.kind, CollectionKind.SNAPSHOT)
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0].available)
        self.assertEqual(items[0].name, "algebra.pdf")


class LinuxDiscoveryTests(StorageTestCase):
    def test_parses_real_storage_and_excludes_pseudo_filesystems(self) -> None:
        mountinfo = self.base / "mountinfo"
        mountinfo.write_text(
            "36 25 8:1 / / rw,relatime - ext4 /dev/sda1 rw\n"
            "37 36 0:5 / /proc rw,nosuid - proc proc rw\n"
            "38 36 8:17 / /media/user/Study rw - ext4 /dev/sdb1 rw\n",
            encoding="utf-8",
        )

        volumes = LinuxVolumeDiscovery(mountinfo).discover()

        self.assertEqual(len(volumes), 2)
        self.assertTrue(volumes[0].is_system)
        self.assertEqual(volumes[1].mount_point, "/media/user/Study")
        self.assertTrue(volumes[1].is_removable)


if __name__ == "__main__":
    unittest.main()
