"""Make every monorepo Python package importable during repository tests."""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent
for domain in ("services", "modules"):
    for source in sorted((PROJECT_ROOT / domain).glob("*/src")):
        sys.path.insert(0, str(source))
