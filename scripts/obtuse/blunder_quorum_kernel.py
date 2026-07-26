from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blunder.deterministic_shards import execute_shard_suite, parse_shard_spec, rank_shard_candidates
from blunder.quorum_graph import build_quorum_graph, impact_frontier
from blunder.quorum_kernel import (
    kernel_snapshot,
    metabolic_plan,
    parse_metabolic_budget,
    parse_vote,
    quorum_decision,
    timeline_report,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Operate the first-party Blunder Quorum Graph Kernel.")
    result.add_argument("action", choices=("snapshot", "graph", "impact", "shards", "tournament", "quorum", "timeline", "metabolism"))
    result.add_argument("--root", required=True)
    result.add_argument("--report", required=True)
    result.add_argument("--input")
    return result


def read_object(path: str | None, label: str) -> dict[str, object]:
    if not path:
        raise ValueError(f"{label} requires --input.")
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} input must be a JSON object.")
    return value


def object_list(value: object, label: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be an object list.")
    return [dict(item) for item in value]


def build_report(arguments: argparse.Namespace) -> dict[str, object]:
    root = Path(arguments.root).resolve()
    if arguments.action == "snapshot":
        return kernel_snapshot(root)
    graph = build_quorum_graph(root, True)
    if arguments.action == "graph":
        return graph
    value = read_object(arguments.input, arguments.action.capitalize())
    if arguments.action == "impact":
        raw_paths = value.get("changedPaths")
        changed_paths = [str(item) for item in raw_paths] if isinstance(raw_paths, list) else []
        maximum_depth = value.get("maximumDepth")
        if not isinstance(maximum_depth, int) or isinstance(maximum_depth, bool):
            raise ValueError("Impact maximumDepth must be an integer.")
        return impact_frontier(graph, changed_paths, maximum_depth)
    raw_reports = object_list(value.get("shardReports"), "shardReports") if "shardReports" in value else []
    if arguments.action == "tournament":
        return rank_shard_candidates(raw_reports)
    if arguments.action == "shards":
        specs = tuple(parse_shard_spec(item) for item in object_list(value.get("specs"), "Shard specs"))
        maximum_parallel = value.get("maximumParallel")
        if not isinstance(maximum_parallel, int) or isinstance(maximum_parallel, bool):
            raise ValueError("Shard suite maximumParallel must be an integer.")
        return execute_shard_suite(root, specs, str(graph.get("fingerprint") or ""), maximum_parallel)
    if arguments.action == "metabolism":
        telemetry = value.get("telemetry")
        budget = value.get("budget")
        if not isinstance(telemetry, dict) or not isinstance(budget, dict):
            raise ValueError("Metabolism requires telemetry and budget objects.")
        return metabolic_plan(graph, telemetry, parse_metabolic_budget(budget))
    candidate = value.get("candidate")
    if not isinstance(candidate, dict):
        raise ValueError(f"{arguments.action.capitalize()} requires a candidate object.")
    votes = tuple(parse_vote(item) for item in object_list(value.get("votes"), "Quorum votes"))
    decision = quorum_decision(root, graph, candidate, votes, raw_reports)
    if arguments.action == "quorum":
        return decision
    return timeline_report(graph, candidate, decision, raw_reports)


def write_report(path: Path, report: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(report), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    arguments = parser().parse_args()
    report = build_report(arguments)
    write_report(Path(arguments.report).resolve(), report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("status") in {
        "PASS",
        "OPERATIONAL",
        "CHANGED",
        "UNCHANGED",
        "READY_FOR_BOUNDED_IDLE_ANALYSIS",
        "PAUSED_RESOURCE_OR_SAFETY_GATE",
        "QUORUM_APPROVED_AWAITING_OPERATOR",
        "QUARANTINED",
        "ROLLBACK_REQUIRED",
        "SEALED",
        "CHAMPION_AVAILABLE_FOR_QUORUM",
        "NO_ELIGIBLE_CANDIDATE",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
