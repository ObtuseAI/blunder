from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from blunder.capabilities import load_registry
from blunder.missions.fabric import plan_program
from blunder.morphogenesis import (
    ARCHETYPES,
    ORGAN_ROLES,
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


REPO_ROOT = Path(__file__).resolve().parents[1]


class BlunderMorphogenesisTests(unittest.TestCase):
    def compile(self, archetype: str) -> dict[str, object]:
        return compile_organism(
            archetype,
            f"Execute the {archetype} held-out task with bounded evidence",
            "A" * 64,
            ("blunder/core.py", "tests/test_core.py"),
            "high",
            "medium",
            ("repository-evidence", "validation-evidence", "dissent-evidence"),
            OrganismBudget(1_800, 3, 200_000, 5.0),
            "fixed-model",
            True,
        )

    def test_six_archetypes_compile_deterministic_bounded_acyclic_specs(self) -> None:
        for archetype in ARCHETYPES:
            with self.subTest(archetype=archetype):
                first = self.compile(archetype)
                second = self.compile(archetype)
                self.assertEqual(first["organismSha256"], second["organismSha256"])
                self.assertEqual(first["admission"]["status"], "ADMITTED_SHADOW")
                self.assertTrue(first["admission"]["acyclic"])
                self.assertTrue(first["admission"]["distinctContexts"])
                self.assertEqual(first["admission"]["claimedPathConflicts"], {})
                self.assertTrue(first["admission"]["allOrgansBounded"])
                self.assertFalse(first["execution"]["latentChannelsEnabled"])
                self.assertTrue(first["execution"]["existingMissionWorktreeRequired"])
                roles = {organ["role"] for organ in first["organs"]}
                self.assertTrue(roles.issubset(set(ORGAN_ROLES)))
                self.assertTrue(all(organ["authority"]["push"] is False for organ in first["organs"]))

    def test_dependency_and_path_conflicts_block_admission_analysis(self) -> None:
        budget = OrganBudget(60, 0, 0, 0.0)
        first = OrganSpec("a", "builder", ("b",), ("blunder/core.py",), (), (), (), budget, "context-a", {})
        second = OrganSpec("b", "builder", ("a",), ("blunder/core.py",), (), (), (), budget, "context-b", {})
        analysis = analyze_organ_specs((first, second))
        self.assertFalse(analysis["acyclic"])
        self.assertFalse(analysis["admissible"])
        self.assertEqual(analysis["claimedPathConflicts"]["blunder/core.py"], ["a", "b"])

    def test_shared_evidence_is_content_addressed_and_dissolution_revokes_authority(self) -> None:
        organism = self.compile("defect-repair")
        organ_id = organism["organs"][0]["id"]
        now = datetime.now(timezone.utc)
        claim = build_evidence_claim(organ_id, "repository-observation", "B" * 64, "C" * 64, 0.8, now, True)
        evidence = shared_evidence_space(organism, (claim,))
        dissolved = lifecycle_event(organism, "DISSOLVED", 8, now, (claim.claim_id,))
        extracted = lifecycle_event(organism, "KNOWLEDGE_EXTRACTED", 9, now, (claim.claim_id,))
        self.assertTrue(evidence["contentAddressedOnly"])
        self.assertFalse(evidence["hiddenPromptStateAccepted"])
        self.assertFalse(evidence["authorityTransferred"])
        self.assertFalse(dissolved["runtimeAuthorityActive"])
        self.assertTrue(dissolved["eligibleEvidencePreserved"])
        self.assertTrue(extracted["eligibleEvidencePreserved"])
        self.assertFalse(extracted["sourceDeleted"])

    def test_shadow_comparison_is_equal_budget_and_refuses_weak_claims(self) -> None:
        organism = self.compile("product")
        budget = {
            "maximumSeconds": 1_800,
            "maximumProviderCalls": 3,
            "maximumTokens": 200_000,
            "maximumCostUsd": 5.0,
        }
        weak = shadow_comparison(budget, organism, (), 12)
        measurements = tuple(
            {"staticSuccess": index < 8, "organismSuccess": index < 9, "safetyRegressions": 0}
            for index in range(12)
        )
        supported = shadow_comparison(budget, organism, measurements, 12)
        self.assertEqual(weak["status"], "INSUFFICIENT_CONFIDENCE")
        self.assertFalse(weak["improvementClaimed"])
        self.assertEqual(supported["status"], "MATCH_OR_BETTER")
        self.assertFalse(supported["promotionAuthorized"])

    def test_static_mission_fabric_exposes_morphogenesis_shadow_without_launching_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            (repository / "blunder").mkdir()
            (repository / "tests").mkdir()
            (repository / "blunder" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
            (repository / "tests" / "test_core.py").write_text("import unittest\n", encoding="utf-8")
            plan = plan_program(repository, load_registry(REPO_ROOT), "Implement and test a product interface feature")
        shadow = plan["morphogenesisShadow"]
        self.assertEqual(shadow["execution"]["mode"], "SHADOW")
        self.assertFalse(shadow["execution"]["newLauncherAuthority"])
        self.assertEqual(plan["morphogenesisComparison"]["status"], "INSUFFICIENT_CONFIDENCE")


if __name__ == "__main__":
    unittest.main()
