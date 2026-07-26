from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from blunder.sovereign_observatory import (
    EVENT_KINDS,
    PROJECTION_KINDS,
    append_event,
    audit_observatory,
    build_event,
    build_projection,
    dunce_consumer_view,
    load_events,
)


PROOF_SCHEMA = "obtuse.blunder.sovereign-observatory-epoch-six-proof.v1"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def span_id(index: int) -> str:
    return f"{index + 1:016x}"


def write_json(path: Path, value: Mapping[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_proof(root: Path, output_root: Path) -> dict[str, object]:
    output_root.mkdir(parents=True, exist_ok=False)
    ledger = output_root / "observatory-events.jsonl"
    started = datetime(2026, 7, 14, tzinfo=timezone.utc)
    events = []
    non_effect_span_ids: list[str] = []
    for index in range(214):
        previous = events[-1] if events else None
        kind = EVENT_KINDS[index % len(EVENT_KINDS)]
        effectful = index < len(EVENT_KINDS)
        attributes: dict[str, object] = {
            "telemetryPresent": True,
            "value": index,
            "information-flow": index,
            "energy": 1.0,
            "entropy": 0.1,
            "health": "healthy",
            "uncertainty": 0.05,
            "authority": "none",
            "mutation-lineage": digest("parent-genome") if kind == "mutation" else None,
            "genome-niches": "repair",
            "knowledge-diffusion": 0.2,
            "pressure": 1.0,
        }
        if kind == "federation":
            attributes["credential"] = "fixture-value-that-must-be-redacted"
        event = build_event(
            sequence=index,
            previous_digest=previous.event_digest if previous is not None else None,
            recorded_at=started + timedelta(microseconds=index),
            monotonic_ns=index,
            trace_id="a" * 32,
            span_id=span_id(index),
            parent_span_id=previous.span_id if previous is not None else None,
            kind=kind,
            retention_class="constitutional" if effectful else "audit",
            redaction_class="sensitive" if kind == "federation" else "internal",
            resource={"service.name": "blunder", "service.version": "ccos-v1"},
            attributes=attributes,
            evidence_digest=digest(f"event-evidence-{index}"),
            effectful=effectful,
            policy_event_digest=digest(f"policy-event-{index}") if effectful else None,
        )
        append_event(ledger, event)
        events.append(event)
        if not effectful:
            non_effect_span_ids.append(event.span_id)
    loaded = load_events(ledger)
    ledger_before = hashlib.sha256(ledger.read_bytes()).hexdigest().upper()
    expected_spans = non_effect_span_ids + ["f" * 16]
    audit = audit_observatory(loaded, expected_spans)
    projections: dict[str, dict[str, object]] = {}
    for projection_kind in PROJECTION_KINDS:
        projection = build_projection(loaded, projection_kind)
        projections[projection_kind] = projection
        write_json(output_root / f"projection-{projection_kind}.json", projection)
    disposable = output_root / "projection-pressure.json"
    disposable.unlink()
    ledger_after = hashlib.sha256(ledger.read_bytes()).hexdigest().upper()
    dunce_view = dunce_consumer_view(projections["authority"])
    errors: list[str] = []
    if len({event.kind for event in loaded}) != len(EVENT_KINDS):
        errors.append("not every governed event kind was captured")
    if audit["effectPolicyCausalCoverage"] != 1.0:
        errors.append("effectful policy and causal coverage is below 100 percent")
    if audit["nonEffectTraceCompleteness"] < 0.995 or audit["missingSpanIds"] != ["f" * 16]:
        errors.append("controlled non-effect span completeness is below threshold or gap is hidden")
    if audit["orphanSpanIds"] or audit["missingTelemetrySpanIds"] or audit["clockSkewSpanIds"]:
        errors.append("observatory integrity audit found an orphan, telemetry gap, or clock skew")
    if audit["redactedValueCount"] < 1 or "fixture-value-that-must-be-redacted" in ledger.read_text(encoding="utf-8"):
        errors.append("sensitive observatory value was not redacted")
    if any(projection["allClaimsEvidenceLinked"] is not True for projection in projections.values()):
        errors.append("a projection claim cannot drill down to evidence")
    if ledger_before != ledger_after or not ledger.exists():
        errors.append("projection deletion changed the evidence substrate")
    if any(dunce_view[key] is not False for key in ("policyMutationAuthorized", "truthMutationAuthorized", "fitnessMutationAuthorized", "promotionMutationAuthorized")):
        errors.append("Dunce projection consumer received mutation authority")
    mutation_event = next(event for event in loaded if event.kind == "mutation")
    simulation_event = next(event for event in loaded if event.kind == "simulation")
    fitness_event = next(event for event in loaded if event.kind == "fitness")
    if not (mutation_event.trace_id == simulation_event.trace_id == fitness_event.trace_id):
        errors.append("mutation lineage is not causally traceable through evaluation and fitness")
    write_json(output_root / "observatory-audit.json", audit)
    write_json(output_root / "dunce-consumer-view.json", dunce_view)
    summary_without_digest: dict[str, object] = {
        "schema": PROOF_SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "root": str(root),
        "eventCount": len(loaded),
        "capturedEventKindCount": len({event.kind for event in loaded}),
        "projectionCount": len(projections),
        "effectPolicyCausalCoverage": audit["effectPolicyCausalCoverage"],
        "nonEffectTraceCompleteness": audit["nonEffectTraceCompleteness"],
        "explicitMissingSpanCount": len(audit["missingSpanIds"]),
        "allProjectionClaimsEvidenceLinked": all(projection["allClaimsEvidenceLinked"] is True for projection in projections.values()),
        "projectionDeletionPreservedLedger": ledger_before == ledger_after,
        "ledgerSha256": ledger_after,
        "providerCalls": 0,
        "errors": errors,
        "authority": {
            "dashboardSourceOfTruth": False,
            "duncePolicyMutation": False,
            "dunceTruthMutation": False,
            "dunceFitnessMutation": False,
            "duncePromotionMutation": False,
            "rawChainOfThoughtCaptured": False,
        },
    }
    summary = summary_without_digest | {"proofSha256": digest(json.dumps(summary_without_digest, sort_keys=True))}
    write_json(output_root / "epoch-six-summary.json", summary)
    if errors:
        raise RuntimeError(f"Epoch Six sovereign-observatory proof failed: {errors}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove Blunder Epoch Six sovereign observatory.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--output-root", required=True)
    arguments = parser.parse_args()
    summary = build_proof(Path(arguments.root).resolve(), Path(arguments.output_root).resolve())
    print(json.dumps(summary, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
