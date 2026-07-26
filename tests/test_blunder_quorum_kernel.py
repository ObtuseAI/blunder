from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from blunder.deterministic_shards import (
    execute_validation_shard,
    parse_shard_spec,
    rank_shard_candidates,
)
from blunder.quorum_graph import build_quorum_graph, canonical_sha256, graph_delta, impact_frontier
from blunder.quorum_kernel import (
    MetabolicBudget,
    kernel_snapshot,
    metabolic_plan,
    parse_vote,
    quorum_decision,
    timeline_report,
)


def write_python(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def passing_vote(role: str, candidate_id: str, proposal_sha256: str) -> dict[str, object]:
    return {
        "role": role,
        "contextId": f"isolated-{role}-context",
        "candidateId": candidate_id,
        "proposalSha256": proposal_sha256,
        "verdict": "APPROVE",
        "evidenceSha256": [hashlib.sha256(f"{role}-evidence".encode("utf-8")).hexdigest().upper()],
        "criticalFindings": [],
    }


class BlunderQuorumKernelTests(unittest.TestCase):
    def test_p31_graph_is_content_addressed_and_source_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_python(root, "blunder/dependency.py", "def value() -> int:\n    return 7\n")
            write_python(root, "blunder/consumer.py", "from blunder.dependency import value\n\ndef use() -> int:\n    return value()\n")
            graph = build_quorum_graph(root, False)
        self.assertEqual(graph["status"], "PASS")
        self.assertEqual(graph["counts"]["files"], 2)
        self.assertGreaterEqual(graph["counts"]["symbols"], 2)
        self.assertGreaterEqual(graph["counts"]["imports"], 1)
        self.assertTrue(graph["authority"]["sourceFilesRemainCanonical"])
        self.assertTrue(graph["authority"]["graphIsDerived"])

    def test_p32_impact_propagates_from_dependency_to_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_python(root, "blunder/dependency.py", "def value() -> int:\n    return 7\n")
            write_python(root, "blunder/consumer.py", "from blunder.dependency import value\n\ndef use() -> int:\n    return value()\n")
            graph = build_quorum_graph(root, False)
            report = impact_frontier(graph, ("blunder/dependency.py",), 4)
        self.assertEqual(report["status"], "PASS")
        self.assertIn("blunder/dependency.py", report["impactedFiles"])
        self.assertIn("blunder/consumer.py", report["impactedFiles"])

    def test_p31_graph_delta_detects_exact_source_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_python(root, "blunder/value.py", "VALUE = 1\n")
            before = build_quorum_graph(root, False)
            write_python(root, "blunder/value.py", "VALUE = 22\n")
            after = build_quorum_graph(root, False)
            delta = graph_delta(before, after)
        self.assertEqual(delta["status"], "CHANGED")
        self.assertEqual(delta["modifiedPaths"], ["blunder/value.py"])

    def test_p33_microspace_rejects_arbitrary_commands_and_seals_repeated_proof(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported commandKind"):
            parse_shard_spec(
                {"id": "unsafe", "commandKind": "shell", "targets": ["whoami"], "repeatCount": 1, "timeoutSeconds": 5, "memoryBytes": 134217728}
            )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_python(root, "blunder/probe.py", "def probe() -> bool:\n    return True\n")
            graph = build_quorum_graph(root, False)
            spec = parse_shard_spec(
                {"id": "compile-probe", "commandKind": "python-compile", "targets": ["blunder/probe.py"], "repeatCount": 2, "timeoutSeconds": 20, "memoryBytes": 268435456}
            )
            report = execute_validation_shard(root, spec, str(graph["fingerprint"]))
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["repeatable"])
        self.assertEqual(report["summary"]["runs"], 2)
        self.assertFalse(report["authority"]["arbitraryCommands"])
        self.assertIn("kernelMemoryLimitGuaranteed", report["authority"])
        charge = report["transcripts"][0]["resourceCharge"]
        self.assertIn(charge["completeness"], {"COMPLETE", "PARTIAL"})
        self.assertEqual(tuple(charge["metrics"]), (
            "processDurationNanoseconds",
            "cpuTimeNanoseconds",
            "peakMemoryBytes",
            "diskReadBytes",
            "diskWriteBytes",
            "queueDepth",
            "queueCapacity",
            "providerCalls",
            "providerTokens",
            "providerCostUsd",
        ))

    def test_p34_tournament_ranks_only_repeatable_passing_shards(self) -> None:
        reports = [
            {"status": "PASS", "repeatable": True, "transcriptSha256": "A" * 64, "spec": {"shard_id": "slow"}, "summary": {"meanElapsedMilliseconds": 10}},
            {"status": "PASS", "repeatable": True, "transcriptSha256": "B" * 64, "spec": {"shard_id": "fast"}, "summary": {"meanElapsedMilliseconds": 5}},
            {"status": "NONDETERMINISTIC", "repeatable": False, "transcriptSha256": "C" * 64, "spec": {"shard_id": "unstable"}, "summary": {"meanElapsedMilliseconds": 1}},
        ]
        result = rank_shard_candidates(reports)
        self.assertEqual(result["status"], "CHAMPION_AVAILABLE_FOR_QUORUM")
        self.assertEqual(result["champion"]["candidateId"], "fast")
        self.assertFalse(result["automaticMerge"])

    def test_p35_p36_p39_quorum_requires_bound_content_and_unanimous_independent_votes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = "def candidate() -> str:\n    return 'review'\n"
            write_python(root, "blunder/candidate.py", content)
            graph = build_quorum_graph(root, False)
            spec = parse_shard_spec(
                {"id": "candidate-proof", "commandKind": "python-compile", "targets": ["blunder/candidate.py"], "repeatCount": 1, "timeoutSeconds": 20, "memoryBytes": 268435456}
            )
            shard = execute_validation_shard(root, spec, str(graph["fingerprint"]))
            change_set = [{"path": "blunder/candidate.py", "contentSha256": hashlib.sha256(content.encode("utf-8")).hexdigest().upper()}]
            proposal_sha256 = canonical_sha256(change_set)
            candidate = {
                "candidateId": "candidate-one",
                "proposalSha256": proposal_sha256,
                "baseGraphFingerprint": graph["fingerprint"],
                "checkpointSha256": "C" * 64,
                "changedPaths": ["blunder/candidate.py"],
                "changeSet": change_set,
                "courtVerdict": "APPROVE",
                "regressionFree": True,
                "appliedToReviewTree": False,
            }
            votes = tuple(parse_vote(passing_vote(role, "candidate-one", proposal_sha256)) for role in ("proposer", "skeptic", "judge"))
            decision = quorum_decision(root, graph, candidate, votes, (shard,))
            rejected_vote = passing_vote("skeptic", "candidate-one", proposal_sha256) | {"verdict": "REJECT", "criticalFindings": ["counterexample remains"]}
            rejected_votes = (
                parse_vote(passing_vote("proposer", "candidate-one", proposal_sha256)),
                parse_vote(rejected_vote),
                parse_vote(passing_vote("judge", "candidate-one", proposal_sha256)),
            )
            quarantine = quorum_decision(root, graph, candidate, rejected_votes, (shard,))
        self.assertEqual(decision["status"], "NO_CHAMPION")
        self.assertFalse(decision["automaticPromotionAuthorized"])
        self.assertFalse(decision["authority"]["operatorConfirmationRequired"])
        self.assertFalse(decision["formalMathematicalProof"])
        self.assertFalse(decision["authority"]["integration"])
        self.assertEqual(quarantine["status"], "QUARANTINED")
        self.assertTrue(quarantine["immuneResponse"]["quarantined"])

    def test_p37_metabolism_requires_real_idle_resource_and_safety_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_python(root, "blunder/probe.py", "VALUE = 1\n")
            graph = build_quorum_graph(root, False)
            budget = MetabolicBudget(3, 180, 35.0, 2 * 1024 * 1024 * 1024)
            telemetry = {
                "operatorIdle": True,
                "foregroundMissionActive": False,
                "cpuPercent": 10.0,
                "availableMemoryBytes": 8 * 1024 * 1024 * 1024,
                "thermalState": "nominal",
                "queueFailures": 0,
                "destructiveAuthority": False,
            }
            ready = metabolic_plan(graph, telemetry, budget)
            paused = metabolic_plan(graph, telemetry | {"operatorIdle": False}, budget)
        self.assertEqual(ready["status"], "READY_FOR_BOUNDED_IDLE_ANALYSIS")
        self.assertGreater(len(ready["selectedTasks"]), 0)
        self.assertEqual(paused["status"], "PAUSED_RESOURCE_OR_SAFETY_GATE")
        self.assertEqual(paused["selectedTasks"], [])

    def test_p38_timeline_is_sealed_without_automatic_merge_or_deletion(self) -> None:
        graph = {"schema": "obtuse.blunder.quorum-semantic-graph.v1", "fingerprint": "A" * 64, "nodes": [], "edges": []}
        candidate = {"candidateId": "candidate-one", "proposalSha256": "B" * 64}
        decision = {"proofSha256": "C" * 64, "status": "QUORUM_APPROVED_AWAITING_OPERATOR"}
        timeline = timeline_report(graph, candidate, decision, ({"status": "PASS", "transcriptSha256": "D" * 64},))
        self.assertEqual(timeline["status"], "SEALED")
        self.assertFalse(timeline["automaticMerge"])
        self.assertFalse(timeline["automaticDeletion"])

    def test_p40_snapshot_is_an_honest_control_plane(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_python(root, "blunder/probe.py", "VALUE = 1\n")
            snapshot = kernel_snapshot(root)
        self.assertEqual(snapshot["status"], "OPERATIONAL")
        self.assertEqual(len(snapshot["surfaces"]), 10)
        self.assertFalse(snapshot["microspaces"]["wasmClaimed"])
        self.assertTrue(snapshot["graph"]["sourceFilesRemainCanonical"])
        self.assertFalse(snapshot["authority"]["automaticMerge"])


if __name__ == "__main__":
    unittest.main()
