from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from blunder.chronos import ChronosContext, start_chronos_context
from blunder.constitutional_kernel import canonical_sha256, load_runtime_constitution
from blunder.physiology import (
    HealthThresholds,
    build_routing_tombstone,
    cognitive_physics,
    default_opportunity_queue,
    derive_health_vector,
    homeostatic_plan,
    select_opportunities,
    tombstone_status,
)
from blunder.resource_accounting import build_resource_charge, measured, sample_local_process


PROOF_SCHEMA = "obtuse.blunder.physiology-epoch-two-b-proof.v1"
LOCAL_SAMPLE_INTERVAL_SECONDS = 0.02


def write_json(path: Path, value: Mapping[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def fixture_charge(context: ChronosContext, cpu_time: int, queue_depth: int | None) -> dict[str, object]:
    metrics = {
        "processDurationNanoseconds": measured(1_000, "ns", "fixture"),
        "cpuTimeNanoseconds": measured(cpu_time, "ns", "fixture"),
        "peakMemoryBytes": measured(100, "bytes", "fixture"),
        "diskReadBytes": measured(25, "bytes", "fixture"),
        "diskWriteBytes": measured(25, "bytes", "fixture"),
    }
    return build_resource_charge(
        f"epoch-two-b:fixture:{cpu_time}:{queue_depth}",
        "physiology-fixture",
        context,
        metrics,
        {"providerCalls": 0, "totalTokens": 0, "costUsd": 0.0},
        "fixture-provider-proof",
        queue_depth,
        10 if queue_depth is not None else None,
        datetime.now(timezone.utc),
    )


def measured_integer(charge: Mapping[str, object], name: str) -> int:
    metrics = charge.get("metrics")
    if not isinstance(metrics, dict):
        raise TypeError("Proof ResourceCharge metrics must be an object.")
    metric = metrics.get(name)
    if not isinstance(metric, dict) or metric.get("state") != "MEASURED":
        raise ValueError(f"Proof requires measured local telemetry: {name}")
    value = metric.get("value")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"Proof local telemetry value is invalid: {name}")
    return int(value)


def build_proof(root: Path, output_root: Path) -> dict[str, object]:
    output_root.mkdir(parents=True, exist_ok=False)
    constitution = load_runtime_constitution()
    context = start_chronos_context(constitution.policy_epoch, 0)
    time.sleep(LOCAL_SAMPLE_INTERVAL_SECONDS)
    local_charge = build_resource_charge(
        "epoch-two-b:local-health",
        "native-mission",
        context,
        sample_local_process(context.mission_started_monotonic_ns),
        {"providerCalls": 0, "totalTokens": 0, "costUsd": 0.0},
        "proof-validated-zero-provider-usage",
        1,
        3,
        datetime.now(timezone.utc),
    )
    local_thresholds = HealthThresholds(
        max(1, measured_integer(local_charge, "cpuTimeNanoseconds") * 2),
        max(1, measured_integer(local_charge, "peakMemoryBytes") * 2),
        0.8,
        max(1, measured_integer(local_charge, "diskWriteBytes") * 2),
        0.8,
        0.6,
    )
    local_health = derive_health_vector(local_charge, local_thresholds, None)
    fixture_context = ChronosContext(context.mission_started_monotonic_ns, constitution.policy_epoch, 0)
    thresholds = HealthThresholds(100, 1_000, 0.8, 1_000, 0.8, 0.6)
    healthy = derive_health_vector(fixture_charge(fixture_context, 20, 1), thresholds, None)
    overloaded = derive_health_vector(fixture_charge(fixture_context, 150, 1), thresholds, "HEALTHY")
    recovery = derive_health_vector(fixture_charge(fixture_context, 70, 1), thresholds, "OVERLOADED")
    unknown = derive_health_vector(fixture_charge(fixture_context, 20, None), thresholds, None)
    homeostasis = {
        status: homeostatic_plan(status, 4, 8)
        for status in ("HEALTHY", "DEGRADED", "OVERLOADED", "STALE", "UNKNOWN", "RECOVERY_BOUND")
    }
    graph = {"fingerprint": "A" * 64, "counts": {"nodes": 20}, "changedPaths": ["blunder/physiology.py"]}
    opportunities = default_opportunity_queue(graph)
    now = datetime.now(timezone.utc)
    tombstone = build_routing_tombstone(
        "semantic-hotspot-map",
        now,
        now + timedelta(hours=1),
        ("B" * 64,),
        "REPEATED_VALIDATION_FAILURE",
        False,
    )
    active_tombstone = tombstone_status(tombstone, now + timedelta(minutes=1))
    expired_tombstone = tombstone_status(tombstone, now + timedelta(hours=2))
    selection = select_opportunities(
        opportunities,
        3,
        120,
        "HEALTHY",
        (tombstone.strategy_id,),
    )
    physics = cognitive_physics(
        (0.5, 0.3, 0.2),
        0.4,
        0.7,
        3.0,
        (
            {"impact": 5.0, "uncertainty": 0.6, "estimatedCost": 2.0},
            {"impact": 2.0, "uncertainty": 0.25, "estimatedCost": 1.0},
        ),
    )
    errors: list[str] = []
    expected_states = (healthy["status"], overloaded["status"], recovery["status"], unknown["status"])
    if expected_states != ("HEALTHY", "OVERLOADED", "RECOVERY_BOUND", "UNKNOWN"):
        errors.append(f"fixture health transitions are invalid: {expected_states}")
    if local_health["status"] != "HEALTHY":
        errors.append(f"calibrated local telemetry did not classify healthy: {local_health['status']}")
    if homeostasis["OVERLOADED"]["authority"]["authorityExpansion"] is not False:
        errors.append("homeostasis expands authority")
    if "QUARANTINE_MUTATION" not in homeostasis["OVERLOADED"]["actions"]:
        errors.append("overload does not quarantine mutation")
    if active_tombstone["routingBlocked"] is not True or expired_tombstone["routingBlocked"] is not False:
        errors.append("routing tombstone is not reversible")
    if active_tombstone["sourceDeleted"] is not False or active_tombstone["replayable"] is not True:
        errors.append("routing tombstone deletes history or loses replay")
    if any(item["id"] == tombstone.strategy_id for item in selection["selected"]):
        errors.append("active tombstone did not block opportunity routing")
    if physics["authority"]["selectionAuthority"] is not False:
        errors.append("cognitive physics gained selection authority")
    artifacts = {
        "local-resource-charge.json": local_charge,
        "local-health-vector.json": local_health,
        "fixture-health-vectors.json": {
            "healthy": healthy,
            "overloaded": overloaded,
            "recovery": recovery,
            "unknown": unknown,
        },
        "homeostatic-plans.json": homeostasis,
        "opportunity-selection.json": selection,
        "routing-tombstone.json": {"active": active_tombstone, "expired": expired_tombstone},
        "cognitive-physics.json": physics,
    }
    for filename, artifact in artifacts.items():
        write_json(output_root / filename, artifact)
    summary_without_digest: dict[str, object] = {
        "schema": PROOF_SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "root": str(root),
        "policyEpoch": constitution.policy_epoch,
        "localTelemetryCompleteness": local_charge["completeness"],
        "localHealthStatus": local_health["status"],
        "fixtureStatuses": list(expected_states),
        "homeostaticStatusCount": len(homeostasis),
        "opportunityCount": len(opportunities),
        "selectedOpportunityCount": len(selection["selected"]),
        "tombstoneReversible": expired_tombstone["routingBlocked"] is False,
        "providerCalls": 0,
        "errors": errors,
        "authority": {
            "readOnlyMeasurement": True,
            "authorityExpansion": False,
            "sourceMutation": False,
            "historyDeletion": False,
        },
    }
    summary = summary_without_digest | {"proofSha256": canonical_sha256(summary_without_digest)}
    write_json(output_root / "epoch-two-b-summary.json", summary)
    if errors:
        raise RuntimeError(f"Epoch Two-B physiology proof failed: {errors}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove Blunder Epoch Two-B physiology regulation.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--output-root", required=True)
    arguments = parser.parse_args()
    summary = build_proof(Path(arguments.root).resolve(), Path(arguments.output_root).resolve())
    print(json.dumps(summary, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
