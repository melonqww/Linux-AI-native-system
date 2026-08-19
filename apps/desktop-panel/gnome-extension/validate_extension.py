#!/usr/bin/env python3
"""Validate the GNOME extension without loading GNOME Shell.

This is intentionally a static/runtime-contract check.  It catches stale
installed copies, missing assets and common GJS integration mistakes before
the extension is enabled in a desktop session.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
UUID = "ai-native-linux@melonqww"


def fail(message: str) -> None:
    print(f"FAIL: {message}")


def check(condition: bool, message: str) -> bool:
    if condition:
        print(f"PASS: {message}")
        return True
    fail(message)
    return False


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--installed",
        action="store_true",
        help="also compare the user-installed extension under ~/.local/share",
    )
    args = parser.parse_args()

    ok = True
    required = ("metadata.json", "extension.js", "stylesheet.css", "install.sh")
    for filename in required:
        ok &= check((ROOT / filename).is_file(), f"repository has {filename}")

    metadata = json.loads((ROOT / "metadata.json").read_text(encoding="utf-8"))
    ok &= check(metadata.get("uuid") == UUID, "metadata UUID is correct")
    ok &= check("46" in metadata.get("shell-version", []), "GNOME 46 is declared")

    source = (ROOT / "extension.js").read_text(encoding="utf-8")
    styles = (ROOT / "stylesheet.css").read_text(encoding="utf-8")
    contracts = (
        ("this.dir.get_child('stylesheet.css')", "stylesheet is resolved from extension directory"),
        ("this._theme.load_stylesheet(this._stylesheet)", "stylesheet is loaded into GNOME theme"),
        ("this._theme.unload_stylesheet(this._stylesheet)", "stylesheet is unloaded on disable"),
        ("this._runtime = runtime", "panel keeps the runtime client"),
        ("new ChatView(this._runtime)", "chat receives the runtime client"),
        ("Main.layoutManager.addChrome", "panel is attached to GNOME Shell"),
        ("this._scroll.set_child(this._messages)", "scroll view uses the GNOME 46 child API"),
    )
    for marker, description in contracts:
        ok &= check(marker in source, description)
    ok &= check("this.get_stylesheet" not in source, "removed unsupported get_stylesheet API")
    ok &= check(".ai-send" in styles and "border-radius: 17px" in styles, "send button is circular")
    ok &= check(".ai-tab-highlight" in styles, "tab highlight styles exist")
    ok &= check(".ai-native-fallback" in styles, "fallback style exists")
    ok &= check("set_spacing" not in source, "StBoxLayout spacing is provided by CSS")

    node = shutil.which("node")
    if node:
        result = subprocess.run(
            [node, "--check", str(ROOT / "extension.js")],
            capture_output=True,
            text=True,
        )
        ok &= check(result.returncode == 0, "JavaScript syntax check passes")
        if result.returncode:
            print(result.stderr.strip())
    else:
        print("SKIP: node is not installed; JavaScript syntax check unavailable")

    if args.installed:
        installed = Path.home() / ".local" / "share" / "gnome-shell" / "extensions" / UUID
        for filename in required[:3]:
            installed_file = installed / filename
            ok &= check(installed_file.is_file(), f"installed copy has {filename}")
            if installed_file.is_file():
                ok &= check(
                    digest(ROOT / filename) == digest(installed_file),
                    f"installed {filename} matches repository",
                )

    print("\nRESULT: READY" if ok else "\nRESULT: FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
