from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from blunder.api.contracts import load_project_policy
from blunder.agent_runtime import AgentBudgetV1, build_agent_team, build_mailbox_message, evaluate_team_shadow
from blunder.environment_automation import OwnedProcessRegistry, detect_project_stacks, reserve_loopback_port
from blunder.research_workflow import DesignConceptV1, RankedExperimentV1, ResearchEvidenceV1, execute_research_design_workflow
from blunder.vnext_controls import compare_resource_policies, consolidate_memory, forward_evaluation_gate, memory_projection, metacognitive_decision


REPO_ROOT = Path(__file__).resolve().parents[1]


class EnvironmentAutomationTests(unittest.TestCase):
    def test_stack_detection_and_loopback_reservation_are_real(self) -> None:
        detected = detect_project_stacks(REPO_ROOT)
        reservation, port = reserve_loopback_port()
        try:
            self.assertIn("python", detected.stacks)
            self.assertIn("pyproject.toml", detected.lockfiles)
            self.assertGreater(port, 0)
            self.assertEqual(reservation.getsockname()[0], "127.0.0.1")
        finally:
            reservation.close()

    def test_process_registry_starts_and_cleans_only_declared_processes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "configs" / "obtuse").mkdir(parents=True)
            policy_path = root / "configs" / "obtuse" / "policy.json"
            shutil.copyfile(REPO_ROOT / "configs" / "obtuse" / "blunder-vnext-project-policy.json", policy_path)
            policy = load_project_policy(root, policy_path)
            command = (sys.executable, "-c", "import time; time.sleep(30)")
            policy = replace(policy, allowed_commands=(command,))
            registry = OwnedProcessRegistry(policy, root / "artifacts")
            process = registry.start("task-process-test", command, root, "terminate-on-cancel")
            time.sleep(0.1)
            self.assertEqual(registry.active()[0].pid, process.pid)
            with self.assertRaisesRegex(PermissionError, "unowned process"):
                registry.stop("process-not-owned")
            stopped = registry.stop(process.process_id)
            self.assertEqual(stopped.pid, process.pid)
            self.assertEqual(registry.active(), ())


class VNextControlTests(unittest.TestCase):
    def test_metacognition_redirects_saturated_uncertain_work_and_never_overrides_gates(self) -> None:
        redirected = metacognitive_decision(0.6, 0.7, 0.05, 0.5, True)
        stopped = metacognitive_decision(1.0, 0.0, 0.0, 1.0, False)
        self.assertEqual(redirected.action, "REDIRECT_STRATEGY")
        self.assertEqual(stopped.action, "STOP_HARD_GATE")

    def test_memory_projection_is_project_scoped_and_authority_closed(self) -> None:
        projected = memory_projection("blunder-private", "failure", "A stale cursor must fail closed.", ("A" * 64,), ())
        self.assertEqual(projected["ccosTier"], "capability")
        self.assertFalse(projected["authority"]["sourceMutation"])
        self.assertEqual(projected["authority"]["providerCalls"], 0)

    def test_offline_memory_consolidation_deduplicates_without_cross_project_or_activation(self) -> None:
        first = memory_projection("blunder-private", "failure", "Never reuse a stale cursor.", ("A" * 64,), ())
        stronger = memory_projection(
            "blunder-private", "failure", "Never reuse a stale cursor.", ("A" * 64, "B" * 64), ()
        )
        result = consolidate_memory("blunder-private", (first, stronger))
        self.assertEqual(len(result["retained"]), 1)
        self.assertEqual(len(result["superseded"]), 1)
        self.assertFalse(result["automaticCandidateActivationAuthorized"])
        with self.assertRaisesRegex(PermissionError, "cross project"):
            consolidate_memory("different-project", (first,))

    def test_forward_evaluation_preserves_exact_no_champion_state(self) -> None:
        result = forward_evaluation_gate((0.8, 0.9), (0.9, 0.95), True, False, True, True)
        self.assertEqual(result["status"], "NO_CHAMPION")
        self.assertFalse(result["automaticPromotionAuthorized"])

    def test_agent_team_records_real_independence_mailboxes_and_threshold_promotion(self) -> None:
        team = build_agent_team(
            ("builder", "code-review", "validator"),
            ("model-a", "model-b", "model-b"),
            ("provider-a", "provider-b", "provider-b"),
            "A" * 64,
            AgentBudgetV1(300, 1, 20_000, 1.0),
        )
        message = build_mailbox_message(
            team,
            "agent-builder",
            "agent-code-review",
            "patch-ready",
            {"patch": "B" * 64},
            ("B" * 64,),
        )
        promoted = evaluate_team_shadow(team, (0.7, 0.72, 0.74), (0.95, 0.96, 0.97), True, True, True)
        self.assertTrue(team.genuine_model_independence)
        self.assertNotEqual(message.sender_causal_identity, message.recipient_context_identity)
        self.assertEqual(promoted["promotionStatus"], "PROMOTED_AUTONOMOUS_THRESHOLD_PASS")

    def test_research_workflow_requires_three_distinct_concepts_and_does_not_upgrade_simulation(self) -> None:
        evidence = tuple(
            ResearchEvidenceV1(f"source-{index}", f"docs/source-{index}.md", f"domain-{index % 2}", f"{index + 1:X}" * 64, True)
            for index in range(3)
        )
        concepts = tuple(
            DesignConceptV1(
                f"concept-{index}",
                f"structure-{index}",
                "Repository-native design direction.",
                "A" * 64,
                "B" * 64,
                "C" * 64,
                0.9,
                0.9,
                0.9,
            )
            for index in range(3)
        )
        experiments = (
            RankedExperimentV1("experiment-a", "concept-0", "Compare the first structure.", "D" * 64, 0.9, 0.8, 1.0, False),
            RankedExperimentV1("experiment-b", "concept-1", "Compare the second structure.", "E" * 64, 0.88, 0.7, 1.0, False),
        )
        result = execute_research_design_workflow("Simplify mission review.", evidence, (), concepts, experiments)
        self.assertEqual(result["measurementClass"], "SIMULATION_OR_MIXED_EVIDENCE")
        self.assertFalse(result["syntheticScoresReportedAsMeasured"])
        self.assertFalse(result["automaticPromotionAuthorized"])

    def test_equal_cost_metacognitive_strategy_comparison_can_promote_without_overriding_gates(self) -> None:
        promoted = compare_resource_policies(
            (0.7, 0.72, 0.74),
            (0.95, 0.96, 0.97),
            True,
            True,
            True,
            2,
            2,
            True,
        )
        blocked = compare_resource_policies(
            (0.7, 0.72, 0.74),
            (0.95, 0.96, 0.97),
            False,
            True,
            True,
            2,
            2,
            True,
        )
        self.assertTrue(promoted["automaticPromotionAuthorized"])
        self.assertFalse(blocked["automaticPromotionAuthorized"])
        self.assertIn("budget", blocked["failedGates"])


if __name__ == "__main__":
    unittest.main()
