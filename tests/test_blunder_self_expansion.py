from __future__ import annotations

import hashlib
import unittest

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


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def need_case():
    signals = tuple(
        GapSignal("missing-evidence-capability", "citation-gap", 0.4, digest(f"gap-{index}"), f"task-{index}")
        for index in range(3)
    )
    return mine_organ_need_case(
        signals=signals,
        existing_organs_tried=("researcher", "validator"),
        counterfactual_evidence=(digest("counterfactual-a"), digest("counterfactual-b")),
        predeclared_fitness_dimension="evidence-recall",
        baseline_value=0.7,
        target_value=0.9,
    )


def organ_design():
    return build_organ_design(
        name="evidence-gap-resolver",
        inputs=("gap-signals", "repository-evidence"),
        outputs=("evidence-claims",),
        tools=("read-source", "search-index"),
        non_root_policy=("read-only", "no-activation"),
        budget=OrganBudget(300, 20_000, 1.0, 1),
        state_contract=("content-addressed-only",),
        evaluators=("historical", "heldout", "adversarial"),
        rollback_contract=("disable-organ", "restore-prior-routing"),
        observability=("inputs", "outputs", "evidence", "cost", "outcome"),
    )


def sealed_evaluation():
    mutation = build_mutation_ir((MutationOperation("topology", "topology.shadow-organ", "bounded-add", {"shadow": True}),))
    lineage = build_candidate_lineage(
        mutation,
        (digest("parent"),),
        "ordinary-selection-v1",
        "Evaluate a typed shadow organ.",
        "Improve evidence recall.",
        digest("rollback"),
        ("ccos-v1",),
    )
    observations = tuple(
        RealityObservation(policy.reality_id, 0.95, 1.0, 1.0, 1.0, 0.6, 0.8, 0.95, digest(policy.reality_id), False, True)
        for policy in standard_reality_policies()
    )
    controls = EvaluationControls(True, True, True, True, True, 0.9, digest("tree"), digest("tree"))
    return evaluate_sealed_candidate(lineage, observations, controls)


def transfers(hard_gates_passed: bool, cost: float) -> tuple[TransferMeasurement, ...]:
    return tuple(
        TransferMeasurement(model, domain, 0.95, cost, hard_gates_passed, digest(f"{model}-{domain}-{cost}"))
        for model in ("model-family-a", "model-family-b")
        for domain in ("repair", "architecture")
    )


class BlunderSelfExpansionTests(unittest.TestCase):
    def test_repeated_measured_gap_requires_counterfactual_evidence(self) -> None:
        need = need_case()
        self.assertEqual(need.observation_count, 3)
        with self.assertRaises(ValueError):
            mine_organ_need_case(
                signals=(GapSignal("bottleneck", "one", 1.0, digest("only"), "task"),),
                existing_organs_tried=("builder",),
                counterfactual_evidence=(digest("counterfactual"),),
                predeclared_fitness_dimension="latency",
                baseline_value=0.5,
                target_value=0.6,
            )

    def test_organ_dsl_is_typed_bounded_and_root_invariants_are_excluded(self) -> None:
        design = organ_design()
        self.assertGreater(design.budget.maximum_seconds, 0)
        with self.assertRaises(PermissionError):
            build_organ_design(
                "invalid",
                ("input",),
                ("root-authority",),
                ("read-source",),
                ("read-only",),
                OrganBudget(1, 1, 0.0, 0),
                ("none",),
                ("test",),
                ("disable",),
                ("outcome",),
            )

    def test_candidates_include_typed_organ_simpler_configuration_and_no_change(self) -> None:
        candidates = expansion_candidates(need_case(), organ_design(), {"contextLimit": 2})
        self.assertEqual({item["kind"] for item in candidates}, {"typed-new-organ", "simpler-configuration-control", "no-new-organ-control"})
        self.assertTrue(all(item["activationAuthorized"] is False for item in candidates))

    def test_selected_organ_survives_realities_transfer_and_exact_disable(self) -> None:
        state = digest("prior-state")
        decision = evaluate_organ_expansion(
            need_case(),
            organ_design(),
            sealed_evaluation(),
            transfers(True, 0.8),
            1.0,
            state,
            state,
        )
        self.assertEqual(decision["status"], "PROMOTED_AUTONOMOUS_THRESHOLD_PASS")
        self.assertTrue(decision["durableParetoValue"])
        self.assertTrue(decision["activationAuthorized"])
        self.assertTrue(decision["automaticPromotionAuthorized"])
        self.assertFalse(decision["constitutionProposalActivationAuthorized"])

    def test_cost_displacement_or_gate_regression_blocks_selection(self) -> None:
        state = digest("prior-state")
        decision = evaluate_organ_expansion(
            need_case(), organ_design(), sealed_evaluation(), transfers(False, 2.0), 1.0, state, state
        )
        self.assertEqual(decision["status"], "NO_CHAMPION")
        self.assertFalse(decision["gates"]["noHardGateRegression"])
        self.assertFalse(decision["gates"]["noCostDisplacement"])

    def test_meta_evolution_is_threshold_gated_and_recovery_remains_invariant_bound(self) -> None:
        blocked = meta_evolution_gate(
            "mutation-operators",
            True,
            False,
            True,
            (digest("meta-a"), digest("meta-b"), digest("meta-c")),
            2,
            2,
            3,
            0.95,
            0.05,
            True,
        )
        self.assertEqual(blocked["status"], "PROMOTION_BLOCKED_THRESHOLD_NOT_MET")
        self.assertFalse(blocked["automaticPromotionAuthorized"])
        self.assertFalse(blocked["rootInvariantMutationAuthorized"])
        governor = build_recovery_governor(digest("genome"), digest("scheduler"), digest("engine"))
        mission = digest("mission-state")
        recovered = invoke_recovery_governor(governor, mission, True, True, True)
        self.assertEqual(recovered["status"], "RECOVERY_BOUND")
        self.assertTrue(recovered["evolutionEngineRolledBackIndependently"])
        self.assertFalse(recovered["missionStateChanged"])
        self.assertEqual(recovered["missionStateBefore"], recovered["missionStateAfter"])


if __name__ == "__main__":
    unittest.main()
