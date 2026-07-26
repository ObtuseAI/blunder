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
    build_quality_diversity_archive,
    evaluate_sealed_candidate,
    standard_reality_policies,
)


PROOF_SCHEMA = "obtuse.blunder.sealed-evolution-epoch-four-proof.v1"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def write_json(path: Path, value: Mapping[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def source_tree_digest(root: Path) -> str:
    source_paths = tuple(
        sorted(
            path
            for source_root in (root / "blunder", root / "tests", root / "scripts" / "obtuse")
            for path in source_root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
    )
    rows = [f"{path.relative_to(root).as_posix()} {hashlib.sha256(path.read_bytes()).hexdigest()}" for path in source_paths]
    return digest("\n".join(rows))


def build_candidate(layer: str, index: int):
    mutation = build_mutation_ir(
        (
            MutationOperation(
                layer=layer,  # type: ignore[arg-type]
                target=f"{layer}.sealed.candidate-{index}",
                operator="bounded-data-replace",
                value={"candidate": index, "authority": "none"},
            ),
        )
    )
    lineage = build_candidate_lineage(
        mutation=mutation,
        parent_digests=(digest("epoch-three-parent"),),
        operator_id="ordinary-selection-v1",
        rationale="Evaluate one bounded mutation layer in the sealed laboratory.",
        expected_effect="Improve empirical fitness while preserving every hard gate.",
        rollback_digest=digest(f"rollback-{index}"),
        compatibility=("ccos-v1", "constitutional-kernel-v1"),
    )
    return mutation, lineage


def build_observations(index: int, failed_reality: str | None) -> tuple[RealityObservation, ...]:
    return tuple(
        RealityObservation(
            reality_id=policy.reality_id,
            success=0.5 if policy.reality_id == failed_reality else 0.94,
            safety=1.0,
            reproducibility=1.0,
            rollback=1.0,
            novelty=round(0.5 + index * 0.1, 2),
            efficiency=round(0.8 - index * 0.05, 2),
            confidence=0.93,
            evidence_digest=digest(f"candidate-{index}-{policy.reality_id}"),
            contaminated=False,
            deterministic=True,
        )
        for policy in standard_reality_policies()
    )


def build_proof(root: Path, output_root: Path) -> dict[str, object]:
    output_root.mkdir(parents=True, exist_ok=False)
    before = source_tree_digest(root)
    layers = ("prompt", "routing", "scheduling")
    evaluations: list[dict[str, object]] = []
    lineages: list[dict[str, object]] = []
    for index, layer in enumerate(layers):
        mutation, lineage = build_candidate(layer, index)
        controls = EvaluationControls(
            static_court_passed=True,
            isolated_worktree=True,
            deterministic_validation_passed=True,
            quorum_passed=True,
            rollback_drill_passed=True,
            champion_floor=0.9,
            live_tree_before=before,
            live_tree_after=before,
        )
        evaluation = evaluate_sealed_candidate(lineage, build_observations(index, "adversarial"), controls)
        evaluations.append(evaluation)
        lineages.append({"mutation": asdict(mutation), "lineage": asdict(lineage)})
        write_json(output_root / f"candidate-{index}-{layer}.json", evaluation)
    archive = build_quality_diversity_archive(evaluations)
    after = source_tree_digest(root)
    errors: list[str] = []
    if before != after:
        errors.append("sealed evaluation changed the source tree")
    if len({item["mutationLayer"] for item in evaluations}) < 3:
        errors.append("fewer than three mutation layers completed evaluation")
    if any(len(item["pipelineStages"]) != 10 for item in evaluations):
        errors.append("a candidate did not complete the full sealed pipeline")
    if any(len(item["realities"]) != 7 for item in evaluations):
        errors.append("a candidate did not complete all seven realities")
    if archive["status"] != "NO_CHAMPION" or archive["preservedCandidateCount"] != 3:
        errors.append("no-champion archive did not preserve every failed lineage")
    if archive["automaticPromotionAuthorized"] is not False:
        errors.append("quality-diversity archive authorized automatic promotion")
    if any(item["syntheticFutureUsedForPromotion"] is not False for item in evaluations):
        errors.append("synthetic future evidence influenced promotion")
    if any(item["activationAuthorized"] is not False for item in evaluations):
        errors.append("a sealed candidate gained activation authority")
    write_json(output_root / "lineages.json", {"candidates": lineages})
    write_json(output_root / "quality-diversity-archive.json", archive)
    summary_without_digest: dict[str, object] = {
        "schema": PROOF_SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "root": str(root),
        "sourceTreeBefore": before,
        "sourceTreeAfter": after,
        "sourceTreeUnchanged": before == after,
        "mutationLayersCompleted": list(layers),
        "candidateCount": len(evaluations),
        "pipelineStageCount": 10,
        "realityCount": 7,
        "archiveStatus": archive["status"],
        "failedLineagesPreserved": archive["failedLineagesPreserved"],
        "providerCalls": 0,
        "errors": errors,
        "authority": {
            "liveTreeMutation": False,
            "automaticPromotion": False,
            "automaticPromotionPolicyAvailable": True,
            "operatorPromotionRequired": False,
            "constitutionalMutation": False,
        },
    }
    summary = summary_without_digest | {"proofSha256": digest(json.dumps(summary_without_digest, sort_keys=True))}
    write_json(output_root / "epoch-four-summary.json", summary)
    if errors:
        raise RuntimeError(f"Epoch Four sealed-evolution proof failed: {errors}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove Blunder Epoch Four sealed recursive evolution.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--output-root", required=True)
    arguments = parser.parse_args()
    summary = build_proof(Path(arguments.root).resolve(), Path(arguments.output_root).resolve())
    print(json.dumps(summary, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
