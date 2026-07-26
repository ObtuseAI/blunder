from __future__ import annotations

from blunder.inflow.models import BlunderInflowRecord, utc_now


def build_feedback_updates(records: list[BlunderInflowRecord], promotions: list[dict[str, object]]) -> list[dict[str, object]]:
    updates: list[dict[str, object]] = []
    failed = [record for record in records if record["risk_flags"] or record["contradiction_flags"]]
    successful_promotions = [promotion for promotion in promotions if promotion["eligible"]]
    if failed:
        updates.append({
            "timestamp": utc_now(),
            "feedback_type": "REJECTION_RULE_STRENGTHENED",
            "reason": "Rejected or contradictory sources increase future scrutiny.",
            "affected_records": [record["record_id"] for record in failed],
        })
    if successful_promotions:
        updates.append({
            "timestamp": utc_now(),
            "feedback_type": "SOURCE_TRUST_INCREASE",
            "reason": "Replayable proof-backed candidates improve source ranking.",
            "skill_tokens": [promotion["skill_token_id"] for promotion in successful_promotions],
        })
    updates.append({
        "timestamp": utc_now(),
        "feedback_type": "SCHEDULER_THRESHOLD_RECORDED",
        "reason": "Low-risk autonomous processing allowed; external effects remain approval-gated.",
        "live_benchmark_launch_allowed": False,
        "production_mutation_allowed": False,
        "guardrail_weakening_allowed": False,
    })
    return updates
