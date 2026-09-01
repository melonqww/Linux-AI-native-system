import sys
from pathlib import Path


LAB_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = LAB_ROOT.parents[1]
sys.path.insert(0, str(LAB_ROOT / "src"))
for domain in ("services", "modules"):
    for source in sorted((PROJECT_ROOT / domain).glob("*/src")):
        sys.path.insert(0, str(source))
