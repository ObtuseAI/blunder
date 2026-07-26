from __future__ import annotations

from pathlib import Path

from blunder.inflow.agent_trace_miner import mine_agent_trace
from blunder.inflow.models import BlunderInflowRecord
from blunder.inflow.proof_artifact_miner import mine_proof_artifact
from blunder.inflow.runtime_telemetry_miner import mine_runtime_telemetry


def extract_capabilities(record: BlunderInflowRecord) -> BlunderInflowRecord:
    text = ""
    if record["normalized_text_path"]:
        text = Path(record["normalized_text_path"]).read_text(encoding="utf-8", errors="replace")
    candidates: list[dict[str, object]] = []
    candidates.extend(mine_proof_artifact(record, text))
    candidates.extend(mine_runtime_telemetry(record, text))
    candidates.extend(mine_agent_trace(record, text))
    if "benchmark" in text.lower():
        candidates.append({
            "candidate_id": f"{record['record_id']}-benchmark-drill",
            "target_type": "BenchmarkDrill",
            "title": "Benchmark artifacts generate drills without live benchmark launch",
            "evidence_record_id": record["record_id"],
            "requires_replay": True,
        })
    if "failure" in text.lower() or "fail" in text.lower():
        candidates.append({
            "candidate_id": f"{record['record_id']}-failure-mode",
            "target_type": "FailureMode",
            "title": "Failure artifact should produce repair strategy and validation rule",
            "evidence_record_id": record["record_id"],
            "requires_replay": True,
        })
    record["capability_candidates"] = candidates
    return record
