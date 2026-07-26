from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def family_name(trial: str) -> str:
    return trial.split("__", 1)[0]


def reward(path: Path) -> float | None:
    if not path.exists():
        return None
    data = read_json(path)
    value = data.get("verifier_result", {}).get("rewards", {}).get("reward")
    return None if value is None else float(value)


def mode_from_result(path: Path) -> str:
    if not path.exists():
        return "unknown"
    data = read_json(path)
    return str(data.get("agent_result", {}).get("metadata", {}).get("blunder", {}).get("benchmark_mode") or "unknown")


def import_job(job: Path, ledger: Path) -> list[dict[str, Any]]:
    root = read_json(job / "result.json")
    existing = read_jsonl(ledger)
    keys = {(row.get("job_root"), row.get("trial_name")) for row in existing}
    records = list(existing)
    best: dict[str, float] = {}
    for row in existing:
        if row.get("reward") is not None:
            fam = str(row["family"])
            best[fam] = max(best.get(fam, -1.0), float(row["reward"]))
    for trial in sorted([item for item in job.iterdir() if item.is_dir()]):
        if (str(job), trial.name) in keys:
            continue
        fam = family_name(trial.name)
        score = reward(trial / "result.json")
        prev = best.get(fam)
        state = "completed" if score is not None else ("running_or_pending" if not root.get("finished_at") else "error")
        record = {
            "schema": "obtuse.family_history.record.v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "family": fam,
            "trial_name": trial.name,
            "job_root": str(job),
            "job_timestamp": job.name,
            "profile": "FullPublicK1" if "fullpublick1" in str(job).lower() else "unknown",
            "benchmark_mode": mode_from_result(trial / "result.json"),
            "model_route": "minimax/minimax-m3",
            "reward": score,
            "result": "PASS_REWARD_1" if score == 1.0 else ("FAIL_REWARD_0" if score == 0.0 else "PARTIAL_OR_UNKNOWN"),
            "state": state,
            "failure_class": "PASS_REWARD_1" if score == 1.0 else ("FAIL_REWARD_0" if score == 0.0 else "STALE_OR_INCOMPLETE"),
            "previous_best": prev,
            "regression_flag": bool(prev is not None and score is not None and score < prev),
            "appliance_used": mode_from_result(trial / "result.json") == "learned-appliance",
            "appliance_version_hash": None,
            "notes": "imported from stopped/partial job; not an official score",
            "evidence_path": str(trial / "result.json") if (trial / "result.json").exists() else str(trial),
        }
        records.append(record)
        if score is not None:
            best[fam] = max(best.get(fam, -1.0), score)
    write_jsonl(ledger, records)
    return records


def summarize(ledger: Path, artifact: Path) -> dict[str, Any]:
    rows = read_jsonl(ledger)
    regressions = [row for row in rows if row.get("regression_flag")]
    targets = [row for row in rows if row.get("reward") == 0.0]
    families = sorted({str(row.get("family")) for row in rows})
    summary = {
        "schema": "obtuse.family_history.summary.v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ledger_path": str(ledger),
        "records": len(rows),
        "families": len(families),
        "reward_zero_targets": len(targets),
        "regressions": len(regressions),
        "status": "PASS",
    }
    write_json(artifact / "family_history_summary.json", summary)
    write_jsonl(artifact / "family_regressions.jsonl", regressions)
    return summary


def export_backlog(ledger: Path, artifact: Path) -> dict[str, Any]:
    rows = read_jsonl(ledger)
    targets = [row for row in rows if row.get("reward") == 0.0]
    write_jsonl(artifact / "family_history_targets.jsonl", targets)
    return {"exported": len(targets), "path": str(artifact / "family_history_targets.jsonl")}


def main() -> int:
    parser = argparse.ArgumentParser(description="Import and query Blunder benchmark family history.")
    parser.add_argument("--mode", required=True, choices=["ImportJob", "Summarize", "FindRegressions", "FindTargets", "ExportBacklog"])
    parser.add_argument("--job-root")
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--artifact-root", required=True)
    args = parser.parse_args()

    ledger = Path(args.ledger).resolve()
    artifact = Path(args.artifact_root).resolve()
    if args.mode == "ImportJob":
        if not args.job_root:
            raise RuntimeError("--job-root is required for ImportJob")
        rows = import_job(Path(args.job_root).resolve(), ledger)
        result = summarize(ledger, artifact)
        result["imported_total_records"] = len(rows)
    elif args.mode == "ExportBacklog":
        result = export_backlog(ledger, artifact)
    else:
        result = summarize(ledger, artifact)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
