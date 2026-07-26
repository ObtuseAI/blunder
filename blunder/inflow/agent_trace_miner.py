from __future__ import annotations

from blunder.inflow.models import BlunderInflowRecord


def mine_agent_trace(record: BlunderInflowRecord, normalized_text: str) -> list[dict[str, object]]:
    if record["source_type"] != "agent_trace":
        return []
    lowered = normalized_text.lower()
    candidates: list[dict[str, object]] = []
    if "assumption" in lowered or "failed" in lowered:
        candidates.append({
            "candidate_id": f"{record['record_id']}-agent-antipattern",
            "target_type": "AntiPattern",
            "title": "Agent-derived assumption requires replay before trust",
            "evidence_record_id": record["record_id"],
            "requires_replay": True,
            "promotion_blocked_until_replay": True,
        })
    if "validated" in lowered or "fixed" in lowered:
        candidates.append({
            "candidate_id": f"{record['record_id']}-workflow-pattern",
            "target_type": "WorkflowPattern",
            "title": "Agent trace workflow candidate gated by controlled fixture",
            "evidence_record_id": record["record_id"],
            "requires_replay": True,
            "promotion_blocked_until_replay": True,
        })
    return candidates
