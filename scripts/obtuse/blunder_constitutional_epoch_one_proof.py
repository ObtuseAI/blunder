"""Build a no-effect Epoch One coverage and parity proof."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Blunder Epoch One constitutional proof.")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    return parser.parse_args()


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    options = parse_arguments()
    root = options.root.resolve()
    output_root = options.output_root.resolve()
    runtime_root = output_root / "fixture-runtime"
    ledger = runtime_root / "artifacts" / "obtuse" / "blunder-constitutional-kernel-v1" / "decisions.jsonl"
    if ledger.exists():
        raise FileExistsError(f"Epoch One proof refuses to append to an existing fixture ledger: {ledger}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from blunder.cognitive_events import build_parity_report, load_decision_events, record_shadow_decision
    from blunder.constitutional_kernel import (
        build_authority_envelope,
        build_causal_identity,
        load_runtime_constitution,
    )
    from blunder.effect_surface_registry import audit_effect_surface_coverage

    constitution = load_runtime_constitution()
    observed_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    for index, (effect, policy) in enumerate(sorted(constitution.effects.items())):
        current_allowed = not policy.always_denied and not policy.approvals
        envelope = build_authority_envelope(
            f"epoch-one-proof-{index}",
            "epoch-one-proof",
            "controlled-no-effect-fixture",
            effect,
            policy.scopes,
            (),
            constitution.immutable_denials,
            (),
            observed_at + timedelta(minutes=5),
        )
        identity = build_causal_identity(
            "epoch-one-proof",
            constitution.policy_epoch,
            constitution.source_revision,
            None,
            None,
            f"effect-{index}-{effect}",
        )
        record_shadow_decision(
            runtime_root,
            envelope,
            identity,
            current_allowed,
            () if current_allowed else ("controlledFixtureRequiresNoOperationalApproval",),
            observed_at,
        )
    parity = build_parity_report(load_decision_events(runtime_root))
    coverage = audit_effect_surface_coverage(root)
    expected_effects = sorted(constitution.effects)
    observed_effects = parity["effects"]
    effect_coverage_complete = observed_effects == expected_effects
    status = (
        "PASS"
        if parity["status"] == "PASS" and coverage["status"] == "PASS" and effect_coverage_complete
        else "FAIL"
    )
    report: dict[str, object] = {
        "schema": "blunder.constitutional-epoch-one-proof.v1",
        "status": status,
        "proofMode": "CONTROLLED_NO_EFFECT_FIXTURES",
        "constitutionDigest": constitution.digest,
        "sourceRevision": constitution.source_revision,
        "effectExecutionCount": 0,
        "providerCalls": 0,
        "sourceMutations": 0,
        "publicationActions": 0,
        "effectCoverageComplete": effect_coverage_complete,
        "expectedEffects": expected_effects,
        "parity": parity,
        "effectSurfaceCoverage": coverage,
    }
    write_json(output_root / "epoch-one-proof.json", report)
    print(json.dumps(report, sort_keys=True))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
