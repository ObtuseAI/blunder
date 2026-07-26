from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blunder.reliability_campaign import ReliabilityLimits, run_live_campaign, write_campaign


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run the first-party Blunder reliability campaign.")
    result.add_argument("--root", required=True)
    result.add_argument("--artifact", required=True)
    result.add_argument("--cycles", required=True, type=int)
    result.add_argument("--interval-seconds", required=True, type=float)
    return result


def main() -> int:
    arguments = parser().parse_args()
    root = Path(arguments.root).resolve()
    limits = ReliabilityLimits(30.0, 0, 0, 0, 1.0)
    report = run_live_campaign(root, limits, arguments.cycles, arguments.interval_seconds)
    write_campaign(Path(arguments.artifact).resolve(), report)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
