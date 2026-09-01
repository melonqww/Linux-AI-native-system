"""Repository-local entry point which imports the production packages in place."""

from __future__ import annotations

import sys
from pathlib import Path


LAB_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = LAB_ROOT.parents[1]
sys.path.insert(0, str(LAB_ROOT / "src"))
for domain in ("services", "modules"):
    for source in sorted((PROJECT_ROOT / domain).glob("*/src")):
        sys.path.insert(0, str(source))

from ai_scenario_lab.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
