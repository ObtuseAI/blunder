from __future__ import annotations

from blunder.inflow.models import BlunderInflowRecord


def mine_runtime_telemetry(record: BlunderInflowRecord, normalized_text: str) -> list[dict[str, object]]:
    lowered = normalized_text.lower()
    if record["source_type"] not in {"runtime_telemetry", "terminal_log"} and "heartbeat" not in lowered:
        return []
    candidates: list[dict[str, object]] = []
    if "stale" in lowered or "heartbeat" in lowered or "orphan" in lowered:
        candidates.append({
            "candidate_id": f"{record['record_id']}-runtime-stability",
            "target_type": "RuntimeStabilityRule",
            "title": "Use stale heartbeat and orphan process signals as scheduler guardrails",
            "evidence_record_id": record["record_id"],
            "requires_replay": True,
        })
    if "provider" in lowered:
        candidates.append({
            "candidate_id": f"{record['record_id']}-provider-routing",
            "target_type": "ProviderRoutingLesson",
            "title": "Provider failure telemetry adjusts routing lessons without spending escalation",
            "evidence_record_id": record["record_id"],
            "requires_replay": True,
        })
    return candidates
