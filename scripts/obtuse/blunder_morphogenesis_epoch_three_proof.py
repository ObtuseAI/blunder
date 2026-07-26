from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from blunder.constitutional_kernel import canonical_sha256
from blunder.morphogenesis import (
    ARCHETYPES,
    LIFECYCLE_STATES,
    OrganBudget,
    OrganSpec,
    OrganismBudget,
    analyze_organ_specs,
    build_evidence_claim,
    compile_organism,
    lifecycle_event,
    shadow_comparison,
    shared_evidence_space,
)


PROOF_SCHEMA = "obtuse.blunder.morphogenesis-epoch-three-proof.v1"


def write_json(path: Path, value: Mapping[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_proof(root: Path, output_root: Path) -> dict[str, object]:
    output_root.mkdir(parents=True, exist_ok=False)
    budget = OrganismBudget(1_800, 3, 200_000, 5.0)
    compiled: dict[str, dict[str, object]] = {}
    deterministic = True
    for archetype in ARCHETYPES:
        arguments = (
            archetype,
            f"Held-out {archetype} contract task",
            "A" * 64,
            (f"blunder/{archetype.replace('-', '_')}.py", f"tests/test_{archetype.replace('-', '_')}.py"),
            "high",
            "medium",
            ("repository-evidence", "validation-evidence", "dissent-evidence"),
            budget,
            "fixed-model",
            True,
        )
        first = compile_organism(*arguments)
        second = compile_organism(*arguments)
        deterministic = deterministic and first["organismSha256"] == second["organismSha256"]
        compiled[archetype] = first
        write_json(output_root / f"organism-{archetype}.json", first)
    conflict_budget = OrganBudget(60, 0, 0, 0.0)
    conflicts = analyze_organ_specs(
        (
            OrganSpec("organ-a", "builder", ("organ-b",), ("blunder/conflict.py",), (), (), (), conflict_budget, "context-a", {}),
            OrganSpec("organ-b", "builder", ("organ-a",), ("blunder/conflict.py",), (), (), (), conflict_budget, "context-b", {}),
        )
    )
    specimen = compiled["defect-repair"]
    observed_at = datetime(2026, 7, 14, 20, 30, tzinfo=timezone.utc)
    first_organ = specimen["organs"][0]
    if not isinstance(first_organ, dict):
        raise TypeError("Compiled organism organ contract is invalid.")
    claim = build_evidence_claim(
        str(first_organ["id"]),
        "held-out-observation",
        "B" * 64,
        "C" * 64,
        0.9,
        observed_at,
        True,
    )
    evidence = shared_evidence_space(specimen, (claim,))
    lifecycle = [
        lifecycle_event(specimen, state, index, observed_at, (claim.claim_id,))
        for index, state in enumerate(LIFECYCLE_STATES, start=1)
    ]
    static_budget = {
        "maximumSeconds": 1_800,
        "maximumProviderCalls": 3,
        "maximumTokens": 200_000,
        "maximumCostUsd": 5.0,
    }
    held_out_measurements = tuple(
        {
            "taskId": f"held-out-{index:02d}",
            "staticSuccess": True,
            "organismSuccess": True,
            "safetyRegressions": 0,
            "measurementKind": "deterministic-contract-replay",
        }
        for index in range(1, 13)
    )
    comparison = shadow_comparison(static_budget, specimen, held_out_measurements, 12)
    weak_comparison = shadow_comparison(static_budget, specimen, held_out_measurements[:3], 12)
    errors: list[str] = []
    for archetype, organism in compiled.items():
        admission = organism.get("admission")
        execution = organism.get("execution")
        if not isinstance(admission, dict) or admission.get("status") != "ADMITTED_SHADOW":
            errors.append(f"{archetype} was not admitted in shadow mode")
        if not isinstance(admission, dict) or admission.get("acyclic") is not True or admission.get("claimedPathConflicts") != {}:
            errors.append(f"{archetype} topology is cyclic or conflictful")
        if not isinstance(execution, dict) or execution.get("existingMissionWorktreeRequired") is not True:
            errors.append(f"{archetype} escaped existing mission worktrees")
        if not isinstance(execution, dict) or execution.get("latentChannelsEnabled") is not False:
            errors.append(f"{archetype} enabled latent channels")
    if not deterministic:
        errors.append("organism compilation is not deterministic")
    if conflicts["admissible"] is not False or not conflicts["claimedPathConflicts"]:
        errors.append("conflict analysis admitted an invalid organism")
    if evidence["contentAddressedOnly"] is not True or evidence["authorityTransferred"] is not False:
        errors.append("shared evidence is not content-addressed or transferred authority")
    if lifecycle[-2]["runtimeAuthorityActive"] is not False or lifecycle[-2]["eligibleEvidencePreserved"] is not True:
        errors.append("dissolution did not revoke authority and preserve eligible evidence")
    if comparison["status"] != "MATCH_OR_BETTER" or comparison["improvementClaimed"] is not False:
        errors.append("equal held-out replay did not match baseline or made an improvement claim")
    if weak_comparison["status"] != "INSUFFICIENT_CONFIDENCE" or weak_comparison["improvementClaimed"] is not False:
        errors.append("weak shadow evidence was upgraded into an improvement claim")
    write_json(output_root / "conflict-analysis.json", conflicts)
    write_json(output_root / "shared-evidence-space.json", evidence)
    write_json(output_root / "lifecycle.json", {"events": lifecycle})
    write_json(output_root / "shadow-comparison.json", comparison)
    write_json(output_root / "weak-shadow-comparison.json", weak_comparison)
    summary_without_digest: dict[str, object] = {
        "schema": PROOF_SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "root": str(root),
        "archetypeCount": len(compiled),
        "deterministic": deterministic,
        "allAcyclic": all(organism["admission"]["acyclic"] is True for organism in compiled.values()),
        "allConflictFree": all(organism["admission"]["claimedPathConflicts"] == {} for organism in compiled.values()),
        "allUseExistingWorktrees": all(
            organism["execution"]["existingMissionWorktreeRequired"] is True for organism in compiled.values()
        ),
        "lifecycleStateCount": len(lifecycle),
        "dissolutionRevokesAuthority": lifecycle[-2]["runtimeAuthorityActive"] is False,
        "eligibleEvidencePreserved": lifecycle[-2]["eligibleEvidencePreserved"] is True,
        "shadowComparisonStatus": comparison["status"],
        "weakEvidenceStatus": weak_comparison["status"],
        "providerCalls": 0,
        "errors": errors,
        "authority": {
            "newLauncherAuthority": False,
            "promotionAuthorized": False,
            "latentChannelsEnabled": False,
            "sourceMutation": False,
        },
    }
    summary = summary_without_digest | {"proofSha256": canonical_sha256(summary_without_digest)}
    write_json(output_root / "epoch-three-summary.json", summary)
    if errors:
        raise RuntimeError(f"Epoch Three morphogenesis proof failed: {errors}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove Blunder Epoch Three morphogenesis.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--output-root", required=True)
    arguments = parser.parse_args()
    summary = build_proof(Path(arguments.root).resolve(), Path(arguments.output_root).resolve())
    print(json.dumps(summary, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
