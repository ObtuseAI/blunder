from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blunder.discovery_engine import build_discovery_brief, write_discovery_brief


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Build a provenance-backed Blunder discovery brief.")
    result.add_argument("--root", required=True)
    result.add_argument("--question", required=True)
    result.add_argument("--report", required=True)
    result.add_argument("--evidence-limit", required=True, type=int)
    return result


def main() -> int:
    arguments = parser().parse_args()
    brief = build_discovery_brief(Path(arguments.root).resolve(), arguments.question, arguments.evidence_limit)
    write_discovery_brief(Path(arguments.report).resolve(), brief)
    print(json.dumps(brief, indent=2))
    return 0 if brief["status"] == "READY_FOR_EXPERIMENT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
