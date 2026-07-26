from __future__ import annotations

from blunder.inflow.models import BlunderInflowRecord


def compute_priority(record: BlunderInflowRecord) -> float:
    current_weakness_match = 1.0 if record["source_type"] in {"failed_validation", "runtime_telemetry"} else 0.45
    recent_failure_match = 1.0 if "failure" in record["source_uri"].lower() or "failed" in record["source_uri"].lower() else 0.35
    proof_value = 1.0 if record["source_type"] in {"proof_ledger", "validation_summary"} else 0.55
    novelty = 0.75 if not record["duplication_flags"] else 0.15
    validator_availability = 0.9 if record["replayability_score"] >= 0.5 else 0.35
    benchmark_relevance = 0.8 if "benchmark" in record["source_uri"].lower() else 0.25
    score = (
        current_weakness_match
        + recent_failure_match
        + record["relevance_score"]
        + proof_value
        + novelty
        + record["replayability_score"]
        + validator_availability
        + benchmark_relevance
        + 0.8
        + 0.85
        - record["risk_score"]
        - (0.5 if record["duplication_flags"] else 0.0)
        - (0.4 if record["freshness_score"] < 0.5 else 0.0)
        - (0.6 if record["authority_score"] < 0.4 else 0.0)
        - (0.5 if record["contradiction_flags"] else 0.0)
        - 0.2
    )
    return round(score, 4)
