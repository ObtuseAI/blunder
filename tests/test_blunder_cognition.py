from __future__ import annotations

import json
import unittest
from pathlib import Path
import tempfile

from blunder.cognition import (
    EvidenceItem,
    LearningRecord,
    PlanCandidate,
    ResearchFinding,
    Strategy,
    build_evidence,
    diversity_score,
    mutation_lens,
    parse_strategy,
    select_plan,
    strategy_report,
)
from blunder.native_agent import build_context_sources
from blunder.native_agent import hydrate_strategy_sources, load_learning_history, normalize_context_path
from blunder.context_compiler import compile_context


def candidate(
    identifier: str,
    lens: str,
    thesis: str,
    target_files: tuple[str, ...],
    evidence_paths: tuple[str, ...],
    validation: tuple[str, ...],
    risks: tuple[str, ...],
) -> PlanCandidate:
    return PlanCandidate(
        identifier=identifier,
        lens=lens,
        thesis=thesis,
        target_files=target_files,
        steps=(f"Inspect evidence for {thesis}", f"Implement and verify {thesis}"),
        evidence_paths=evidence_paths,
        validation=validation,
        rollback="Restore the bounded pre-change snapshot after any failed gate.",
        risks=risks,
        risk_controls=tuple(f"Control {risk} with an explicit validation gate." for risk in risks),
        prediction=f"The validation gates for {thesis} will pass without weakening boundaries.",
        novelty=f"This {lens} route creates distinct leverage for {thesis}.",
    )


class BlunderCognitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = (
            EvidenceItem("blunder/native_agent.py", "a1", 1.0, ("agent",), "agent source"),
            EvidenceItem("tests/test_agent.py", "b2", 0.8, ("test",), "test source"),
        )
        self.candidates = (
            candidate(
                "p1",
                "precision",
                "tighten response parsing",
                ("blunder/native_agent.py",),
                ("blunder/native_agent.py",),
                ("compile",),
                ("provider shape drift",),
            ),
            candidate(
                "p2",
                "systems",
                "build an evidence plan tournament",
                ("blunder/native_agent.py", "tests/test_agent.py"),
                ("blunder/native_agent.py", "tests/test_agent.py"),
                ("compile", "integration suite", "rollback exercise"),
                ("selection bias", "schema drift", "excess complexity"),
            ),
            candidate(
                "p3",
                "adversarial",
                "reject ungrounded mutations",
                ("blunder/native_agent.py",),
                ("blunder/native_agent.py",),
                ("negative boundary suite", "compile"),
                ("false rejection", "path mismatch"),
            ),
            candidate(
                "p4",
                "synthesis",
                "combine prediction errors with future selection",
                ("blunder/native_agent.py",),
                ("blunder/native_agent.py",),
                ("learning replay", "compile"),
                ("stale history", "overfitting"),
            ),
        )
        self.strategy = Strategy(
            findings=(
                ResearchFinding(
                    claim="The runner uses one proposal stage.",
                    evidence_paths=("blunder/native_agent.py",),
                    uncertainty="Provider behavior is not exercised in this fixture.",
                ),
            ),
            candidates=self.candidates,
            discovery_gaps=("Live provider response quality is unknown.",),
        )

    def test_build_evidence_is_deterministic_and_does_not_mutate_sources(self) -> None:
        sources = {"blunder/a.py": "def plan_task(value: str) -> str:\n    return value\n"}
        original = dict(sources)
        first = build_evidence("plan task", sources, 120)
        second = build_evidence("plan task", sources, 120)
        self.assertEqual(first, second)
        self.assertEqual(sources, original)
        self.assertEqual(first[0].matched_terms, ("plan", "task"))

    def test_context_retrieval_discovers_terms_inside_opaque_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "blunder").mkdir()
            (root / "blunder" / "opaque.py").write_text("rare_signal = 'cobalt'\n", encoding="utf-8")
            (root / "blunder" / "unrelated.py").write_text("value = 'plain'\n", encoding="utf-8")
            sources = build_context_sources(root, "repair cobalt discovery")
        self.assertIn("blunder/opaque.py", sources)
        self.assertEqual(next(iter(sources)), "blunder/opaque.py")

    def test_line_qualified_provider_citation_normalizes_only_to_observed_context(self) -> None:
        observed = frozenset({"blunder/native_agent.py", "docs/report.md"})
        self.assertEqual(
            normalize_context_path("blunder/native_agent.py:line 473", observed),
            "blunder/native_agent.py",
        )
        self.assertEqual(normalize_context_path("docs/report.md:35", observed), "docs/report.md")
        self.assertEqual(
            normalize_context_path("docs/report.md: '| P10 | cache evidence |'", observed),
            "docs/report.md",
        )
        self.assertEqual(normalize_context_path("missing.py:line 9", observed), "missing.py:line 9")

    def test_semantic_context_retrieves_middle_ranges_and_reuses_content_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "blunder" / "semantic.py"
            target.parent.mkdir()
            target.write_text("noise = 1\n" * 5000 + "\ndef rare_cobalt_signal() -> bool:\n    return True\n" + "noise = 2\n" * 5000, encoding="utf-8")
            first = compile_context(root, "rare cobalt signal", 20_000, 4_000)
            second = compile_context(root, "rare cobalt signal", 20_000, 4_000)
        self.assertIn("rare_cobalt_signal", first["sources"]["blunder/semantic.py"])
        self.assertFalse(first["manifest"]["cacheHit"])
        self.assertTrue(second["manifest"]["cacheHit"])
        self.assertLess(first["manifest"]["estimatedTokens"], 5000)

    def test_mission_contract_does_not_displace_operator_retrieval_intent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "blunder").mkdir()
            (root / "blunder" / "operator_target.py").write_text("cobalt_signal = True\n" + "x = 1\n" * 9000, encoding="utf-8")
            (root / "blunder" / "contract_noise.py").write_text("workflow permission runtime proof\n" * 3000, encoding="utf-8")
            task = "repair cobalt signal\n\nMISSION BRANCH CONTRACT\nworkflow permission runtime proof " * 20
            sources = build_context_sources(root, task)
        self.assertEqual(next(iter(sources)), "blunder/operator_target.py")

    def test_provider_discovered_repository_evidence_is_hydrated_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "blunder" / "context_identity.py"
            target.parent.mkdir()
            target.write_text("repository_fingerprint = True\n", encoding="utf-8")
            strategy = Strategy(
                (ResearchFinding("cache identity", ("blunder/context_identity.py",), "verified locally"),),
                tuple(
                    candidate(
                        f"p{index}",
                        lens,
                        "cache identity",
                        ("blunder/context_identity.py",),
                        ("blunder/context_identity.py",),
                        ("compile",),
                        ("stale cache",),
                    )
                    for index, lens in enumerate(("precision", "systems", "adversarial", "synthesis"), start=1)
                ),
                ("none",),
            )
            hydrated = hydrate_strategy_sources(root, "cache identity", {}, strategy)
        self.assertEqual(hydrated["blunder/context_identity.py"], "repository_fingerprint = True\n")

    def test_provider_discovery_uses_reserved_context_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "blunder").mkdir()
            target = root / "blunder" / "discovered.py"
            target.write_text("request_digest = True\n" * 200, encoding="utf-8")
            strategy = Strategy(
                (ResearchFinding("request digest", ("blunder/discovered.py",), "verified locally"),),
                tuple(
                    candidate(
                        f"reserve-{index}",
                        lens,
                        "request digest",
                        ("blunder/discovered.py",),
                        ("blunder/discovered.py",),
                        ("compile",),
                        ("stale cache",),
                    )
                    for index, lens in enumerate(("precision", "systems", "adversarial", "synthesis"), start=1)
                ),
                ("none",),
            )
            hydrated = hydrate_strategy_sources(root, "request digest", {"README.md": "x" * 60_000}, strategy)
        self.assertIn("blunder/discovered.py", hydrated)

    def test_context_budget_preserves_breadth_across_ranked_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "blunder").mkdir()
            for index in range(12):
                (root / "blunder" / f"budget_{index}.py").write_text(
                    f"mission_budget_{index} = True\n" + "evidence = 'bounded'\n" * 2000,
                    encoding="utf-8",
                )
            sources = build_context_sources(root, "mission budget evidence")
        self.assertGreaterEqual(len(sources), 8)
        self.assertTrue(all(len(content) < 15000 for content in sources.values()))

    def test_mutation_history_teaches_cognition_from_existing_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "scripts" / "obtuse" / "planner.ps1"
            target.parent.mkdir(parents=True)
            target.write_text("$result = 'PASS'\n", encoding="utf-8")
            ledger = (
                root
                / "artifacts"
                / "obtuse"
                / "blunder-flash-full-autonomy-compounding-bundle-v1"
                / "self-coding"
                / "self_coding_patch_ledger.jsonl"
            )
            ledger.parent.mkdir(parents=True)
            rows = (
                {"patchApplied": True, "sourceLinesChanged": 4, "result": "PASS", "patchType": "QUEUE_REGISTRY", "filesChanged": [str(target)]},
                {"patchApplied": False, "sourceLinesChanged": 0, "result": "SKIPPED_THRESHOLD_PRECHECK_FAILED", "patchType": "FAILURE_ROLLBACK", "rollbackSnapshot": {"items": [{"source": str(target)}]}},
            )
            ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            history = load_learning_history(root)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].lens, "systems")
        self.assertEqual(history[0].status, "APPLIED_AND_VALIDATED")
        self.assertEqual(history[1].lens, "adversarial")
        self.assertEqual(history[1].status, "ROLLED_BACK")
        self.assertEqual(mutation_lens("COMPOUND_NOVELTY"), "synthesis")

    def test_strategy_parser_requires_all_distinct_reasoning_lenses(self) -> None:
        value = {
            "findings": [{"claim": "claim", "evidencePaths": ["a.py"], "uncertainty": "unknown"}],
            "candidates": [
                {
                    "id": f"p{index}",
                    "lens": "precision",
                    "thesis": "thesis",
                    "targetFiles": ["a.py"],
                    "steps": ["inspect", "change"],
                    "evidencePaths": ["a.py"],
                    "validation": ["test"],
                    "rollback": "restore snapshot",
                    "risks": ["risk"],
                    "riskControls": ["control"],
                    "prediction": "test passes",
                    "novelty": "different mechanism",
                }
                for index in range(4)
            ],
            "discoveryGaps": ["gap"],
        }
        strategy = parse_strategy(value)
        with self.assertRaisesRegex(ValueError, "lenses must be exactly"):
            select_plan(strategy, (EvidenceItem("a.py", "a", 1.0, (), "a"),), (), 6)

    def test_strategy_parser_accepts_an_honest_empty_discovery_gap_list(self) -> None:
        value = {
            "findings": [{"claim": "claim", "evidencePaths": ["a.py"], "uncertainty": "none"}],
            "candidates": [
                {
                    "id": lens,
                    "lens": lens,
                    "thesis": "thesis",
                    "targetFiles": ["a.py"],
                    "steps": ["inspect", "change"],
                    "evidencePaths": ["a.py"],
                    "validation": ["test"],
                    "rollback": "restore the original source content",
                    "risks": ["risk"],
                    "riskControls": ["control"],
                    "prediction": "test passes",
                    "novelty": "distinct bounded mechanism",
                }
                for lens in ("precision", "systems", "adversarial", "synthesis")
            ],
            "discoveryGaps": [],
        }
        self.assertEqual(parse_strategy(value).discovery_gaps, ())

    def test_tournament_selects_supported_testable_systems_plan(self) -> None:
        decision = select_plan(self.strategy, self.evidence, (), 6)
        self.assertEqual(decision.selected.identifier, "p2")
        self.assertEqual(decision.scores[0].identifier, "p2")
        self.assertGreater(decision.scores[0].evidence, decision.scores[-1].evidence)

    def test_tournament_rejects_evidence_that_was_not_observed(self) -> None:
        unsupported = candidate(
            "p1",
            "precision",
            "invent an answer",
            ("blunder/native_agent.py",),
            ("missing.py",),
            ("compile",),
            ("fabrication",),
        )
        strategy = Strategy(self.strategy.findings, (unsupported, *self.candidates[1:]), self.strategy.discovery_gaps)
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            select_plan(strategy, self.evidence, (), 6)

    def test_diversity_rewards_a_materially_different_approach(self) -> None:
        copied = candidate(
            "copy",
            "systems",
            self.candidates[0].thesis,
            self.candidates[0].target_files,
            self.candidates[0].evidence_paths,
            self.candidates[0].validation,
            self.candidates[0].risks,
        )
        different = self.candidates[3]
        self.assertGreater(
            diversity_score(different, (self.candidates[0], different)),
            diversity_score(copied, (self.candidates[0], copied)),
        )

    def test_failed_history_penalizes_repeating_the_same_surface(self) -> None:
        history = tuple(
            LearningRecord("ROLLED_BACK", ("blunder/native_agent.py",), "precision") for _ in range(4)
        )
        decision = select_plan(self.strategy, self.evidence, history, 6)
        precision = next(score for score in decision.scores if score.identifier == "p1")
        self.assertEqual(precision.history_adjustment, -5.0)

    def test_report_preserves_provenance_without_copying_source_excerpts(self) -> None:
        decision = select_plan(self.strategy, self.evidence, (), 6)
        report = strategy_report(self.strategy, decision, self.evidence)
        research = report["research"]
        self.assertIsInstance(research, dict)
        self.assertEqual(research["evidence"][0]["excerpt"], "<bounded-context>")


if __name__ == "__main__":
    unittest.main()
