from __future__ import annotations

from blunder.inflow.models import BlunderInflowRecord, stable_id


def build_replay_fixtures(record: BlunderInflowRecord) -> BlunderInflowRecord:
    fixtures: list[dict[str, object]] = []
    for candidate in record["capability_candidates"]:
        fixture_id = stable_id([record["record_id"], str(candidate["candidate_id"]), "fixture"])
        fixtures.append({
            "fixture_id": fixture_id,
            "candidate_id": candidate["candidate_id"],
            "controlled": True,
            "external_effects": False,
            "live_benchmark_launch": False,
            "production_mutation": False,
            "validation_command": "python -m unittest discover -s tests",
            "expected_result": "PASS",
        })
    record["replay_fixtures"] = fixtures
    if fixtures:
        record["validation_refs"].append("REPLAY_FIXTURE_CREATED")
    return record
