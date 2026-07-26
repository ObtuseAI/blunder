from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from blunder.evolution.sealed_lab import (
    EvaluationControls,
    MutationOperation,
    RealityObservation,
    build_candidate_lineage,
    build_mutation_ir,
    evaluate_sealed_candidate,
    standard_reality_policies,
)
from blunder.self_expansion import (
    META_EVOLUTION_LAYERS,
    GapSignal,
    OrganBudget,
    TransferMeasurement,
    build_organ_design,
    build_recovery_governor,
    evaluate_organ_expansion,
    expansion_candidates,
    invoke_recovery_governor,
    meta_evolution_gate,
    mine_organ_need_case,
)


PROOF_SCHEMA = "obtuse.blunder.self-expansion-epoch-seven-proof.v1"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def write_json(path: Path, value: Mapping[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sealed_shadow_evaluation() -> dict[str, object]:
    mutation = build_mutation_ir(
        (MutationOperation("topology", "topology.evidence-gap-resolver-shadow", "bounded-add", {"shadow": True}),)
    )
    lineage = build_candidate_lineage(
        mutation,
        (digest("known-good-genome"),),
        "ordinary-selection-v1",
        "Test an evidence-gap organ in sealed shadow mode.",
        "Improve evidence recall without gate regression.",
        digest("organ-rollback"),
        ("ccos-v1",),
    )
    observations = tuple(
        RealityObservation(
            policy.reality_id,
            0.96,
            1.0,
            1.0,
            1.0,
            0.7,
            0.85,
            0.95,
            digest(f"organ-{policy.reality_id}"),
            False,
            True,
        )
        for policy in standard_reality_policies()
    )
    controls = EvaluationControls(True, True, True, True, True, 0.9, digest("live-tree"), digest("live-tree"))
    return evaluate_sealed_candidate(lineage, observations, controls)


def build_proof(root: Path, output_root: Path) -> dict[str, object]:
    output_root.mkdir(parents=True, exist_ok=False)
    signals = tuple(
        GapSignal(
            "missing-evidence-capability",
            "cross-file-citation-gap",
            0.42 + index * 0.01,
            digest(f"gap-observation-{index}"),
            f"heldout-task-{index}",
        )
        for index in range(4)
    )
    need = mine_organ_need_case(
        signals,
        ("researcher", "validator", "critic"),
        (digest("no-researcher-counterfactual"), digest("configuration-counterfactual")),
        "evidence-recall",
        0.72,
        0.9,
    )
    organ = build_organ_design(
        "evidence-gap-resolver",
        ("gap-signals", "repository-evidence"),
        ("content-addressed-evidence-claims",),
        ("read-source", "search-index"),
        ("read-only", "no-activation", "no-private-state"),
        OrganBudget(300, 20_000, 1.0, 1),
        ("content-addressed-only",),
        ("historical", "heldout", "adversarial", "rollback"),
        ("disable-organ", "restore-prior-routing"),
        ("inputs", "outputs", "evidence", "tools", "cost", "outcome"),
    )
    candidates = expansion_candidates(need, organ, {"contextRetrievalBreadth": 2})
    sealed = sealed_shadow_evaluation()
    transfers = tuple(
        TransferMeasurement(
            model,
            domain,
            0.95,
            0.8,
            True,
            digest(f"transfer-{model}-{domain}"),
        )
        for model in ("model-family-a", "model-family-b")
        for domain in ("defect-repair", "architecture")
    )
    prior_state = digest("prior-state")
    decision = evaluate_organ_expansion(need, organ, sealed, transfers, 1.0, prior_state, prior_state)
    meta_layers = {
        layer: meta_evolution_gate(
            layer,
            True,
            True,
            True,
            tuple(digest(f"meta-{layer}-{index}") for index in range(3)),
            2,
            2,
            3,
            0.95,
            0.05,
            True,
        )
        for layer in META_EVOLUTION_LAYERS
    }
    blocked_meta = meta_evolution_gate(
        "mutation-operators",
        True,
        False,
        True,
        tuple(digest(f"blocked-meta-{index}") for index in range(3)),
        2,
        2,
        3,
        0.95,
        0.05,
        True,
    )
    governor = build_recovery_governor(
        digest("known-good-genome"),
        digest("baseline-scheduler"),
        digest("baseline-evolution-engine"),
    )
    mission_state = digest("mission-state")
    recovery = invoke_recovery_governor(governor, mission_state, True, True, True)
    errors: list[str] = []
    if need.observation_count < 3 or len(need.counterfactual_evidence) < 2:
        errors.append("organ need is not supported by repeated measured and counterfactual evidence")
    if {item["kind"] for item in candidates} != {"typed-new-organ", "simpler-configuration-control", "no-new-organ-control"}:
        errors.append("expansion candidates omit the organ, simpler control, or no-change control")
    if decision["status"] != "PROMOTED_AUTONOMOUS_THRESHOLD_PASS" or decision["durableParetoValue"] is not True:
        errors.append("typed organ did not pass autonomous promotion thresholds")
    if len(decision["modelFamilies"]) < 2 or len(decision["taskDomains"]) < 2:
        errors.append("typed organ did not transfer across two model families and domains")
    if any(decision["gates"][key] is not True for key in decision["gates"]):
        errors.append("typed organ failed a sealed, transfer, cost, or rollback gate")
    if decision["activationAuthorized"] is not True or decision["constitutionProposalActivationAuthorized"] is not False:
        errors.append("organ promotion failed to activate its projection or activated a constitutional proposal")
    if blocked_meta["status"] != "PROMOTION_BLOCKED_THRESHOLD_NOT_MET":
        errors.append("meta-evolution ignored a failed higher-order holdout")
    if any(value["rootInvariantMutationAuthorized"] is not False or value["activationAuthorized"] is not True for value in meta_layers.values()):
        errors.append("meta-evolution failed autonomous projection activation or gained root mutation authority")
    if recovery["missionStateChanged"] is not False or recovery["evolutionEngineRolledBackIndependently"] is not True:
        errors.append("recovery governor did not independently restore evolution while preserving mission state")
    if recovery["activeGenomeDigest"] != governor.known_good_genome_digest or recovery["evolvedSchedulersEnabled"] is not False:
        errors.append("recovery governor did not restore the known-good genome and disable evolved scheduling")
    write_json(output_root / "organ-need-case.json", asdict(need))
    write_json(output_root / "organ-design.json", asdict(organ))
    write_json(output_root / "candidate-controls.json", {"candidates": list(candidates)})
    write_json(output_root / "sealed-shadow-evaluation.json", sealed)
    write_json(output_root / "organ-expansion-decision.json", decision)
    write_json(output_root / "meta-evolution-layers.json", {"layers": meta_layers, "blockedFixture": blocked_meta})
    write_json(output_root / "recovery-governor.json", asdict(governor))
    write_json(output_root / "recovery-result.json", recovery)
    summary_without_digest: dict[str, object] = {
        "schema": PROOF_SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "root": str(root),
        "measuredGapObservationCount": need.observation_count,
        "counterfactualEvidenceCount": len(need.counterfactual_evidence),
        "candidateCount": len(candidates),
        "selectedCandidateStatus": decision["status"],
        "modelFamilyCount": len(decision["modelFamilies"]),
        "taskDomainCount": len(decision["taskDomains"]),
        "exactDisableRollback": decision["gates"]["exactDisableRollback"],
        "metaEvolutionLayerCount": len(meta_layers),
        "higherOrderHoldoutFailureStatus": blocked_meta["status"],
        "recoveryStatus": recovery["status"],
        "missionStatePreserved": recovery["missionStateChanged"] is False,
        "providerCalls": 0,
        "errors": errors,
        "authority": {
            "organPromotedProjectionActivation": decision["activationAuthorized"],
            "metaEvolutionPromotedProjectionActivation": all(
                value["activationAuthorized"] is True for value in meta_layers.values()
            ),
            "rootInvariantMutation": False,
            "constitutionProposalActivation": False,
            "liveSourceMutation": False,
            "externalEffect": False,
        },
    }
    summary = summary_without_digest | {"proofSha256": digest(json.dumps(summary_without_digest, sort_keys=True))}
    write_json(output_root / "epoch-seven-summary.json", summary)
    if errors:
        raise RuntimeError(f"Epoch Seven self-expansion proof failed: {errors}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove Blunder Epoch Seven self-expansion and meta-evolution.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--output-root", required=True)
    arguments = parser.parse_args()
    summary = build_proof(Path(arguments.root).resolve(), Path(arguments.output_root).resolve())
    print(json.dumps(summary, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
