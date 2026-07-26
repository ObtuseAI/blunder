from __future__ import annotations

from blunder.inflow.models import BlunderInflowRecord, stable_id


def generate_synthetic_drills(records: list[BlunderInflowRecord]) -> list[dict[str, object]]:
    drills: list[dict[str, object]] = []
    for record in records:
        for candidate in record["capability_candidates"]:
            if candidate["target_type"] in {"FailureMode", "BenchmarkDrill", "RuntimeStabilityRule"}:
                drills.append({
                    "drill_id": stable_id([record["record_id"], str(candidate["candidate_id"]), "drill"]),
                    "source_record_id": record["record_id"],
                    "candidate_id": candidate["candidate_id"],
                    "target_type": candidate["target_type"],
                    "controlled": True,
                    "launches_live_benchmark": False,
                    "purpose": "Regression prevention through replayable synthetic drill.",
                })
    return drills
