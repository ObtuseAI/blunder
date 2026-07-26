from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from blunder.api.contracts import public_value
from blunder.promotion_cli import run as run_promotion_cli
from blunder.promotion_policy import (
    EVIDENCE_SCHEMA,
    PromotionEvidenceV1,
    PromotionLedger,
    PromotionScopeV1,
    constitutional_promotion_policy,
    evaluate_autonomous_promotion,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def passing_evidence(candidate: str) -> PromotionEvidenceV1:
    return PromotionEvidenceV1(
        EVIDENCE_SCHEMA,
        candidate,
        digest(f"evaluation-{candidate}"),
        (digest(f"evidence-{candidate}-a"), digest(f"evidence-{candidate}-b"), digest(f"evidence-{candidate}-c")),
        {
            "constitutional": True,
            "safety": True,
            "provenance": True,
            "rollback": True,
            "authority": True,
            "budget": True,
            "reproducibility": True,
        },
        1.0,
        0.97,
        0.03,
        3,
        2,
        2,
        3,
        1.0,
        0.1,
        1.0,
        False,
        (),
        True,
        True,
        True,
        True,
        True,
        True,
    )


class AutonomousPromotionPolicyTests(unittest.TestCase):
    def test_every_scope_has_a_content_identified_constitutional_policy(self) -> None:
        policies = tuple(constitutional_promotion_policy(scope) for scope in PromotionScopeV1)
        self.assertEqual(len(policies), len(PromotionScopeV1))
        self.assertEqual(len({policy.policy_digest for policy in policies}), len(policies))
        self.assertTrue(all(policy.minimum_evidence_completeness == 1.0 for policy in policies))

    def test_complete_evidence_promotes_without_manual_path_or_external_authority(self) -> None:
        decision = evaluate_autonomous_promotion(PromotionScopeV1.ORGAN, passing_evidence("organ-candidate"))
        self.assertEqual(decision.status, "PROMOTED_AUTONOMOUS_THRESHOLD_PASS")
        self.assertTrue(decision.promoted)
        self.assertTrue(decision.automatic_promotion_authorized)
        self.assertTrue(decision.promoted_projection_activation_authorized)
        self.assertFalse(decision.source_mutation_authorized)
        self.assertFalse(decision.authority_expansion_authorized)
        self.assertFalse(decision.external_effect_authorized)

    def test_one_failed_threshold_prevents_promotion_and_preserves_no_champion(self) -> None:
        evidence = passing_evidence("improvement-candidate")
        blocked = PromotionEvidenceV1(
            evidence.schema,
            evidence.candidate_identity,
            evidence.evaluation_identity,
            evidence.evidence_digests,
            evidence.hard_gates,
            evidence.evidence_completeness,
            evidence.confidence,
            evidence.uncertainty,
            evidence.independent_sources,
            evidence.independent_models,
            evidence.domains,
            evidence.holdout_trials,
            evidence.holdout_success_rate,
            evidence.improvement_ratio,
            evidence.cost_ratio,
            True,
            evidence.contradictions,
            evidence.rollback_proven,
            evidence.provenance_complete,
            evidence.authority_within_envelope,
            evidence.budget_within_limit,
            evidence.source_unchanged,
            evidence.reproducible,
        )
        decision = evaluate_autonomous_promotion(PromotionScopeV1.IMPROVEMENT, blocked)
        self.assertEqual(decision.status, "NO_CHAMPION")
        self.assertIn("uncontaminated", decision.failed_gates)
        self.assertFalse(decision.automatic_promotion_authorized)

    def test_hard_gate_names_cannot_be_removed_or_replaced(self) -> None:
        evidence = passing_evidence("candidate")
        malformed = PromotionEvidenceV1(
            evidence.schema,
            evidence.candidate_identity,
            evidence.evaluation_identity,
            evidence.evidence_digests,
            {"safety": True},
            evidence.evidence_completeness,
            evidence.confidence,
            evidence.uncertainty,
            evidence.independent_sources,
            evidence.independent_models,
            evidence.domains,
            evidence.holdout_trials,
            evidence.holdout_success_rate,
            evidence.improvement_ratio,
            evidence.cost_ratio,
            evidence.contaminated,
            evidence.contradictions,
            evidence.rollback_proven,
            evidence.provenance_complete,
            evidence.authority_within_envelope,
            evidence.budget_within_limit,
            evidence.source_unchanged,
            evidence.reproducible,
        )
        with self.assertRaisesRegex(ValueError, "exactly match"):
            evaluate_autonomous_promotion(PromotionScopeV1.CAPABILITY, malformed)


class AutonomousPromotionLedgerTests(unittest.TestCase):
    def test_ledger_is_idempotent_hash_chained_and_tamper_evident(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = PromotionLedger(root)
            first = evaluate_autonomous_promotion(PromotionScopeV1.CAPABILITY, passing_evidence("capability-a"))
            second = evaluate_autonomous_promotion(PromotionScopeV1.MEMORY, passing_evidence("memory-b"))
            first_record = ledger.append(first, datetime.now(timezone.utc))
            repeated = ledger.append(first, datetime.now(timezone.utc))
            second_record = ledger.append(second, datetime.now(timezone.utc))
            self.assertEqual(first_record["recordDigest"], repeated["recordDigest"])
            self.assertEqual(second_record["previousRecordDigest"], first_record["recordDigest"])
            self.assertEqual(ledger.state()["sequence"], 2)
            path = sorted((root / "artifacts" / "obtuse" / "blunder-vnext-v1" / "promotions" / "records").glob("*.json"))[0]
            value = json.loads(path.read_text(encoding="utf-8"))
            value["decision"]["promoted"] = False
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "digest does not match"):
                PromotionLedger(root).state()

    def test_cli_reads_repository_local_evidence_and_returns_nonzero_when_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_path = root / "evidence.json"
            evidence_path.write_text(json.dumps(public_value(passing_evidence("cli-organ"))), encoding="utf-8")
            passed, passed_code = run_promotion_cli(
                ["--root", str(root), "--scope", "organ", "--evidence", str(evidence_path)]
            )
            self.assertEqual(passed_code, 0)
            self.assertTrue(passed["decision"]["promoted"])
            blocked_value = public_value(passing_evidence("cli-release"))
            self.assertIsInstance(blocked_value, dict)
            blocked_value["hardGates"]["rollback"] = False
            evidence_path.write_text(json.dumps(blocked_value), encoding="utf-8")
            blocked, blocked_code = run_promotion_cli(
                ["--root", str(root), "--scope", "release-candidate", "--evidence", str(evidence_path)]
            )
            self.assertEqual(blocked_code, 2)
            self.assertFalse(blocked["decision"]["promoted"])


if __name__ == "__main__":
    unittest.main()
