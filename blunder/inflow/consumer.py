from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

from blunder.inflow.inflow_scheduler import run_scheduler
from blunder.inflow.models import SchedulerMode


def parse_mode(value: str) -> SchedulerMode:
    modes = {"AuditOnly", "RunOnce", "IngestOnly", "ExtractOnly", "ReplayOnly", "PromoteEligibleOnly", "IdleAutonomySafeLoop"}
    if value not in modes:
        raise ValueError(f"Unsupported scheduler mode: {value}")
    return value  # type: ignore[return-value]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="BLUNDER_RECURSIVE_DATA_INFLOW_MESH_V2 consumer")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--mode", required=True)
    args = parser.parse_args(argv)
    mode = parse_mode(args.mode)
    result = run_scheduler(Path(args.repo_root), Path(args.artifact_root), mode)
    print(json.dumps({"mode": mode, "validation_summary": result["validation_summary"], "scoreboard": result["scoreboard"]}, indent=2, sort_keys=True))
    return 0 if result["validation_summary"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
