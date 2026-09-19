from __future__ import annotations

import argparse
import sys
from pathlib import Path


LAB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_ROOT / "src"))

from ai_scenario_lab.public_evidence import export_foundation_history  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export privacy-safe AI Scenario Lab evidence for Git"
    )
    parser.add_argument(
        "--reports",
        type=Path,
        default=LAB_ROOT / "reports" / "foundation",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=LAB_ROOT / "public-evidence",
    )
    arguments = parser.parse_args()
    written = export_foundation_history(arguments.reports, arguments.output)
    run_count = sum(path.suffix == ".json" for path in written) - 1
    print(f"PUBLIC EVIDENCE: {run_count} runs")
    print(f"INDEX: {written[0]}")
    print(f"TABLES: {arguments.output / 'README.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
