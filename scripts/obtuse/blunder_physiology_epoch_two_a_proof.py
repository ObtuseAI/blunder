from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from blunder.chronos import ChronosContext, build_chronos_stamp, start_chronos_context
from blunder.constitutional_kernel import canonical_sha256, load_runtime_constitution
from blunder.resource_accounting import (
    RESOURCE_CHARGE_SCHEMA,
    RESOURCE_METRIC_NAMES,
    build_resource_charge,
    sample_local_process,
)
from blunder.validation_broker import brokered_run


PROOF_SCHEMA = "obtuse.blunder.physiology-epoch-two-a-proof.v1"


def write_json(path: Path, value: Mapping[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def charge_errors(charge: Mapping[str, object], label: str) -> list[str]:
    errors: list[str] = []
    metrics = charge.get("metrics")
    if charge.get("schema") != RESOURCE_CHARGE_SCHEMA:
        errors.append(f"{label} schema is invalid")
    if charge.get("completeness") not in {"COMPLETE", "PARTIAL"}:
        errors.append(f"{label} completeness is invalid")
    if not isinstance(metrics, dict) or tuple(metrics) != RESOURCE_METRIC_NAMES:
        errors.append(f"{label} resource vector is incomplete or reordered")
    elif any(
        not isinstance(value, dict) or value.get("state") not in {"MEASURED", "UNMEASURED"}
        for value in metrics.values()
    ):
        errors.append(f"{label} contains an invalid telemetry state")
    authority = charge.get("authority")
    if not isinstance(authority, dict) or authority.get("readOnlySampling") is not True:
        errors.append(f"{label} does not prove read-only sampling")
    if isinstance(authority, dict) and (
        authority.get("optimization") is not False
        or authority.get("homeostaticAction") is not False
        or authority.get("providerAuthority") is not False
    ):
        errors.append(f"{label} exceeds Epoch Two-A authority")
    return errors


def build_proof(root: Path, output_root: Path) -> dict[str, object]:
    output_root.mkdir(parents=True, exist_ok=False)
    constitution = load_runtime_constitution()
    fixed_event = datetime(2026, 7, 14, 20, 0, tzinfo=timezone.utc)
    chronos = build_chronos_stamp(
        ChronosContext(1_000, constitution.policy_epoch, 0),
        5_000,
        fixed_event,
        fixed_event - timedelta(seconds=1),
        5.0,
    )
    local_context = start_chronos_context(constitution.policy_epoch, 0)
    local_charge = build_resource_charge(
        "epoch-two-a:local-process",
        "native-mission",
        local_context,
        sample_local_process(local_context.mission_started_monotonic_ns),
        {"providerCalls": 0, "totalTokens": 0, "costUsd": 0.0},
        "proof-validated-zero-provider-usage",
        1,
        3,
        datetime.now(timezone.utc),
    )
    broker_context = start_chronos_context(constitution.policy_epoch, 0)
    broker = brokered_run(
        (sys.executable, "-c", "print('epoch-two-a-broker-proof')"),
        root,
        dict(os.environ),
        30,
        128 * 1024 * 1024,
    )
    broker_charge = build_resource_charge(
        "epoch-two-a:validation-broker",
        "validation-shard",
        broker_context,
        broker["telemetry"],
        {"providerCalls": 0, "totalTokens": 0, "costUsd": 0.0},
        "validation-authority",
        None,
        None,
        datetime.now(timezone.utc),
    )
    errors = charge_errors(local_charge, "local charge") + charge_errors(broker_charge, "broker charge")
    if chronos.get("policyEpoch") != constitution.policy_epoch:
        errors.append("Chronos policy epoch does not match the runtime constitution")
    if chronos.get("evolutionGeneration") != 0:
        errors.append("Chronos founding evolution generation is not zero")
    freshness = chronos.get("evidenceFreshness")
    if not isinstance(freshness, dict) or freshness.get("state") != "FRESH":
        errors.append("Chronos evidence freshness fixture is not deterministic")
    if broker["returncode"] != 0:
        errors.append(f"validation broker exited with {broker['returncode']}")
    write_json(output_root / "chronos-contract.json", chronos)
    write_json(output_root / "local-process-resource-charge.json", local_charge)
    write_json(output_root / "broker-resource-charge.json", broker_charge)
    summary_without_digest: dict[str, object] = {
        "schema": PROOF_SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "root": str(root),
        "policyEpoch": constitution.policy_epoch,
        "evolutionGeneration": 0,
        "resourceMetricCount": len(RESOURCE_METRIC_NAMES),
        "localCompleteness": local_charge["completeness"],
        "brokerCompleteness": broker_charge["completeness"],
        "brokerUnmeasured": broker_charge["unmeasured"],
        "providerCalls": 0,
        "errors": errors,
        "authority": {
            "readOnlySampling": True,
            "optimization": False,
            "healthClassification": False,
            "homeostaticAction": False,
            "sourceMutation": False,
        },
    }
    summary = summary_without_digest | {"proofSha256": canonical_sha256(summary_without_digest)}
    write_json(output_root / "epoch-two-a-summary.json", summary)
    if errors:
        raise RuntimeError(f"Epoch Two-A physiology proof failed: {errors}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove Blunder Epoch Two-A physiology contracts.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--output-root", required=True)
    arguments = parser.parse_args()
    root = Path(arguments.root).resolve()
    output_root = Path(arguments.output_root).resolve()
    summary = build_proof(root, output_root)
    print(json.dumps(summary, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
