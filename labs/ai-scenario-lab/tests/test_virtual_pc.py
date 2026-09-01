import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from ai_scenario_lab.virtual_pc import VirtualComputer, VirtualPathError


LAB_ROOT = Path(__file__).resolve().parents[1]


def test_virtual_pc_maps_only_enrolled_paths_and_requires_a_marker_to_reset():
    root = LAB_ROOT / ".runtime" / "unit" / str(uuid4()) / "virtual-pc"
    pc = VirtualComputer(root, lab_root=LAB_ROOT)
    try:
        pc.provision(LAB_ROOT / "fixtures" / "base-desktop.json")
        document = pc.physical_path("/home/test-user/Documents/algebra.pdf")
        assert document.is_file()
        assert pc.virtual_path(document) == "/home/test-user/Documents/algebra.pdf"
        with pytest.raises(VirtualPathError):
            pc.physical_path("/home/test-user/../../outside")
        with pytest.raises(VirtualPathError):
            pc.physical_path("C:/Users/real-user")
        (root / VirtualComputer.MARKER).unlink()
        with pytest.raises(VirtualPathError, match="unmarked"):
            pc.provision(LAB_ROOT / "fixtures" / "base-desktop.json")
    finally:
        managed_run = root.parents[1]
        if managed_run.is_relative_to(LAB_ROOT / ".runtime" / "unit"):
            shutil.rmtree(managed_run, ignore_errors=True)


def test_virtual_pc_rejects_a_root_outside_the_lab():
    with pytest.raises(VirtualPathError):
        VirtualComputer(LAB_ROOT.parent / "virtual-pc", lab_root=LAB_ROOT)
