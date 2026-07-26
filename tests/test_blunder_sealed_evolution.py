from __future__ import annotations

import hashlib
import unittest

from blunder.evolution.sealed_lab import (
    EvaluationControls,
    MutationOperation,
    RealityObservation,
    build_candidate_lineage,
    build_mutation_ir,
    build_quality_diversity_archive,
    evaluate_mutation_operator,
    evaluate_sealed_candidate,
    standard_reality_policies,
)


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest().upper()


def observations(failed_reality: str | None, synthetic_success: float) -> tuple[RealityObservation, ...]:
    return tuple(
        RealityObservation(
            reality_id=policy.reality_id,
            success=0.4 if policy.reality_id == failed_reality else synthetic_success if policy.synthetic else 0.95,
            safety=1.0,
            reproducibility=1.0,
            rollback=1.0,
            novelty=0.6,
            efficiency=0.8,
            confidence=0.9,
            evidence_digest=digest(f"evidence-{policy.reality_id}-{failed_reality}"),
            contaminated=False,
            deterministic=True,
        )
        for policy in standard_reality_policies()
    )


def candidate(layer: str, suffix: str):
    mutation = build_mutation_ir(
        (
            MutationOperation(
                layer=layer,  # type: ignore[arg-type]
                target=f"{layer}.policy.{suffix}",
                operator="bounded-replace",
                value={"mode": suffix},
            ),
        )
    )
    return build_candidate_lineage(
        mutation=mutation,
        parent_digests=(digest("parent"),),
        operator_id="ordinary-selection-v1",
        rationale="Test a bounded data-only policy mutation.",
        expected_effect="Improve held-out task completion without weakening gates.",
        rollback_digest=digest("rollback"),
        compatibility=("ccos-v1",),
    )


def controls(tree_after: str, champion_floor: float) -> EvaluationControls:
    return EvaluationControls(
        static_court_passed=True,
        isolated_worktree=True,
        deterministic_validation_passed=True,
        quorum_passed=True,
        rollback_drill_passed=True,
        champion_floor=champion_floor,
        live_tree_before=digest("live-tree"),
        live_tree_after=tree_after,
    )


class BlunderSealedEvolutionTests(unittest.TestCase):
    def test_mutation_ir_is_deterministic_and_rejects_immutable_targets(self) -> None:
        first = candidate("prompt", "concise")
        second = candidate("prompt", "concise")
        self.assertEqual(first, second)
        with self.assertRaises(PermissionError):
            build_mutation_ir(
                (
                    MutationOperation(
                        layer="validation",
                        target="constitution.authority",
                        operator="replace",
                        value=False,
                    ),
                )
            )

    def test_seven_reality_pipeline_promotes_only_after_all_thresholds_pass(self) -> None:
        result = evaluate_sealed_candidate(
            lineage=candidate("routing", "specialist"),
            observations=observations(None, 0.0),
            controls=controls(digest("live-tree"), 0.9),
        )
        self.assertTrue(result["promotionEligible"])
        self.assertTrue(result["activationAuthorized"])
        self.assertTrue(result["automaticPromotionAuthorized"])
        self.assertFalse(result["syntheticFutureUsedForPromotion"])
        self.assertEqual(result["promotionStatus"], "PROMOTED_AUTONOMOUS_THRESHOLD_PASS")
        self.assertFalse(result["operatorReviewRequired"])
        self.assertEqual(len(result["realities"]), 7)
        self.assertEqual(len(result["pipelineStages"]), 10)

    def test_synthetic_future_cannot_rescue_failed_required_reality(self) -> None:
        result = evaluate_sealed_candidate(
            lineage=candidate("scheduling", "fair-share"),
            observations=observations("fresh-heldout", 1.0),
            controls=controls(digest("live-tree"), 0.9),
        )
        self.assertFalse(result["promotionEligible"])
        self.assertEqual(result["promotionStatus"], "NO_CHAMPION")

    def test_live_tree_change_and_failed_lineages_remain_fail_closed_in_archive(self) -> None:
        layers = ("prompt", "routing", "scheduling")
        results = [
            evaluate_sealed_candidate(
                lineage=candidate(layer, f"candidate-{index}"),
                observations=observations("adversarial", 1.0),
                controls=controls(digest("changed-tree") if index == 0 else digest("live-tree"), 0.9),
            )
            for index, layer in enumerate(layers)
        ]
        archive = build_quality_diversity_archive(results)
        self.assertEqual(archive["status"], "NO_CHAMPION")
        self.assertEqual(archive["preservedCandidateCount"], 3)
        self.assertEqual(archive["failedLineagesPreserved"], 3)
        self.assertFalse(archive["automaticPromotionAuthorized"])

    def test_mutation_operator_evaluation_waits_for_ordinary_selection_proof(self) -> None:
        with self.assertRaises(PermissionError):
            evaluate_mutation_operator(False, (digest("ordinary"),))
        allowed = evaluate_mutation_operator(True, (digest("ordinary"),))
        self.assertFalse(allowed["operatorMutationAuthorized"])


if __name__ == "__main__":
    unittest.main()
