"""Read-only discovery of Linux mount points and Windows drives."""

from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
from pathlib import Path
from typing import Protocol

from .contracts import DiscoveredVolume


PSEUDO_FILESYSTEMS = frozenset(
    {
        "autofs",
        "bpf",
        "cgroup",
        "cgroup2",
        "configfs",
        "debugfs",
        "devpts",
        "devtmpfs",
        "fusectl",
        "hugetlbfs",
        "mqueue",
        "proc",
        "pstore",
        "securityfs",
        "squashfs",
        "sysfs",
        "tmpfs",
        "tracefs",
    }
)
NETWORK_FILESYSTEMS = frozenset({"9p", "cifs", "nfs", "nfs4", "sshfs"})


class VolumeDiscovery(Protocol):
    def discover(self) -> list[DiscoveredVolume]: ...


def stable_volume_id(identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"volume-{digest}"


def _linux_device_identity(device: str) -> str:
    device_path = Path(device)
    if not device.startswith("/dev/"):
        return device
    try:
        resolved_device = device_path.resolve(strict=True)
    except OSError:
        return device
    for link_directory in (Path("/dev/disk/by-uuid"), Path("/dev/disk/by-id")):
        try:
            links = list(link_directory.iterdir())
        except OSError:
            continue
        for link in links:
            try:
                if link.resolve(strict=True) == resolved_device:
                    return f"{link_directory.name}:{link.name}"
            except OSError:
                continue
    return device


def _decode_mount_field(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _disk_capacity(mount_point: str) -> tuple[int, int]:
    try:
        usage = shutil.disk_usage(mount_point)
    except OSError:
        return 0, 0
    return usage.total, usage.free


class LinuxVolumeDiscovery:
    def __init__(self, mountinfo_path: Path = Path("/proc/self/mountinfo")) -> None:
        self.mountinfo_path = mountinfo_path

    def discover(self) -> list[DiscoveredVolume]:
        volumes: dict[str, DiscoveredVolume] = {}
        for line in self.mountinfo_path.read_text(encoding="utf-8").splitlines():
            before, separator, after = line.partition(" - ")
            if not separator:
                continue
            fields = before.split()
            filesystem_fields = after.split()
            if len(fields) < 5 or len(filesystem_fields) < 2:
                continue
            mount_point = _decode_mount_field(fields[4])
            mount_root = _decode_mount_field(fields[3])
            fs_type = filesystem_fields[0]
            device = _decode_mount_field(filesystem_fields[1])
            if fs_type in PSEUDO_FILESYSTEMS:
                continue

            is_system = mount_point == "/"
            is_network = fs_type in NETWORK_FILESYSTEMS or ":" in device
            is_removable = mount_point.startswith(("/media/", "/run/media/"))
            identity = f"linux:{fs_type}:{_linux_device_identity(device)}:{mount_root}"
            volume_id = stable_volume_id(identity)
            name = "System" if is_system else (Path(mount_point).name or device)
            total_bytes, free_bytes = _disk_capacity(mount_point)
            candidate = DiscoveredVolume(
                volume_id=volume_id,
                name=name,
                mount_point=mount_point,
                device=device,
                fs_type=fs_type,
                is_system=is_system,
                is_removable=is_removable,
                is_network=is_network,
                total_bytes=total_bytes,
                free_bytes=free_bytes,
            )
            existing = volumes.get(volume_id)
            if existing is None or candidate.is_system:
                volumes[volume_id] = candidate
        return sorted(volumes.values(), key=lambda volume: (not volume.is_system, volume.mount_point))


class WindowsVolumeDiscovery:
    DRIVE_REMOVABLE = 2
    DRIVE_FIXED = 3
    DRIVE_REMOTE = 4

    @staticmethod
    def _identity(mount_point: str) -> str:
        serial = ctypes.c_ulong()
        success = ctypes.windll.kernel32.GetVolumeInformationW(
            mount_point,
            None,
            0,
            ctypes.byref(serial),
            None,
            None,
            None,
            0,
        )
        if success:
            return f"serial:{serial.value:08x}"
        return f"mount:{mount_point}"

    def discover(self) -> list[DiscoveredVolume]:
        drive_mask = ctypes.windll.kernel32.GetLogicalDrives()
        system_drive = os.environ.get("SystemDrive", "C:").upper().rstrip("\\")
        volumes: list[DiscoveredVolume] = []
        for index in range(26):
            if not drive_mask & (1 << index):
                continue
            letter = chr(ord("A") + index)
            mount_point = f"{letter}:\\"
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(mount_point)
            if drive_type not in {self.DRIVE_REMOVABLE, self.DRIVE_FIXED, self.DRIVE_REMOTE}:
                continue
            is_system = f"{letter}:" == system_drive
            is_network = drive_type == self.DRIVE_REMOTE
            is_removable = drive_type == self.DRIVE_REMOVABLE
            identity = f"windows:{self._identity(mount_point)}"
            total_bytes, free_bytes = _disk_capacity(mount_point)
            volumes.append(
                DiscoveredVolume(
                    volume_id=stable_volume_id(identity),
                    name="System" if is_system else f"Drive {letter}",
                    mount_point=mount_point,
                    device=mount_point,
                    fs_type="windows",
                    is_system=is_system,
                    is_removable=is_removable,
                    is_network=is_network,
                    total_bytes=total_bytes,
                    free_bytes=free_bytes,
                )
            )
        return sorted(volumes, key=lambda volume: (not volume.is_system, volume.mount_point))


class PlatformVolumeDiscovery:
    def discover(self) -> list[DiscoveredVolume]:
        if os.name == "nt":
            return WindowsVolumeDiscovery().discover()
        return LinuxVolumeDiscovery().discover()
