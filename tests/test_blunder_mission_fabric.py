from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from blunder.capabilities import load_registry
from blunder.execution_budget import ExecutionBudget
from blunder.missions.branches import MissionBranchManager
from blunder.missions.fabric import MissionFabricManager, plan_program
from blunder.missions.outcomes import outcome_summary, record_outcome
from blunder.native_agent import Proposal, ProposedChange, request_review


REPO_ROOT = Path(__file__).resolve().parents[1]


def git(root: Path, *arguments: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return process.stdout.strip()


def initialize_repository(root: Path) -> None:
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "fabric@example.invalid")
    git(root, "config", "user.name", "Blunder Fabric")
    for relative in (
        "blunder/core.py",
        "blunder/ui.py",
        "tests/test_core.py",
        "tests/test_ui.py",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "import unittest\n\nclass Proof(unittest.TestCase):\n    def test_true(self):\n        self.assertTrue(True)\n"
            if relative.startswith("tests/")
            else f"value = '{path.stem}'\n"
        )
        path.write_text(content, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-m", "seed")


def configure_runtime(runtime: Path, repository: Path, workspace: Path) -> None:
    path = runtime / "artifacts" / "obtuse" / "blunder-shell-v2" / "mission-branches.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema": "obtuse.blunder.mission-branch-config.v1",
                "repositoryRoot": str(repository),
                "workspaceRoot": str(workspace),
                "maxParallel": 3,
                "maxMissionSeconds": 300,
                "maxProviderCalls": 3,
                "maxTokens": 100000,
                "maxCostUsd": 2.0,
                "retentionDays": 7,
            }
        ),
        encoding="utf-8",
    )


class BlunderMissionFabricTests(unittest.TestCase):
    def test_program_plan_builds_non_overlapping_specialists_and_synthesis_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            initialize_repository(repository)
            plan = plan_program(repository, load_registry(REPO_ROOT), "Implement and test a cohesive core interface feature")
        specialists = [node for node in plan["nodes"] if node["role"] == "specialist"]
        synthesis = [node for node in plan["nodes"] if node["role"] == "synthesis"][0]
        claims = [path for node in specialists for path in node["claimedPaths"]]
        self.assertGreaterEqual(len(specialists), 2)
        self.assertEqual(len(claims), len(set(claims)))
        self.assertEqual(set(synthesis["dependsOn"]), {node["id"] for node in specialists})

    def test_program_runs_specialists_then_creates_validated_synthesis_branch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            runtime = base / "runtime"
            repository = base / "repository"
            workspace = base / "worktrees"
            runtime.mkdir()
            repository.mkdir()
            initialize_repository(repository)
            configure_runtime(runtime, repository, workspace)
            worker = runtime / "scripts" / "obtuse" / "blunder_mission_worker.py"
            worker.parent.mkdir(parents=True)
            worker.write_text(
                """from __future__ import annotations
import argparse
import json
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--root', required=True)
parser.add_argument('--task', required=True)
parser.add_argument('--result', required=True)
parser.add_argument('--provider-approved', action='store_true')
parser.add_argument('--identity-token', required=True)
args = parser.parse_args()
claims = args.task.split('You exclusively own these paths: ', 1)[1].split('. Do not modify', 1)[0].split(', ')
target = Path(args.root, claims[0])
target.write_text(target.read_text(encoding='utf-8') + 'specialist = True\\n', encoding='utf-8')
Path(args.result).write_text(json.dumps({'ok': True, 'report': {'status': 'APPLIED_AND_VALIDATED', 'independentReview': {'verdict': 'APPROVE'}}}), encoding='utf-8')
""",
                encoding="utf-8",
            )
            registry = load_registry(REPO_ROOT)
            branches = MissionBranchManager(runtime, registry, sys.executable)
            fabric = MissionFabricManager(runtime, registry, branches)
            program = fabric.create("Implement and test a cohesive core interface feature", True)
            deadline = time.monotonic() + 15
            current: dict[str, object] | None = None
            while time.monotonic() < deadline:
                current = fabric.get(str(program["id"]))
                if current["status"] in {"ready_for_synthesis", "failed"}:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(current)
            assert current is not None
            self.assertEqual(current["status"], "ready_for_synthesis")
            synthesized = fabric.synthesize(str(program["id"]))
            self.assertEqual(synthesized["status"], "completed")
            self.assertEqual(synthesized["synthesis"]["integrationState"], "READY_TO_APPLY")

    def test_budget_and_outcome_learning_are_enforced_and_aggregated(self) -> None:
        budget = ExecutionBudget(300, 1, 100, 1.0, None)
        budget.begin_provider("strategy")
        budget.complete_provider("strategy", {"prompt_tokens": 30, "completion_tokens": 20, "cost": 0.1})
        with self.assertRaisesRegex(RuntimeError, "provider-call budget"):
            budget.begin_provider("implementation")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mission = {
                "id": "m-proof",
                "programId": "p-proof",
                "capabilities": {"skills": [{"id": "engineering:build-with-proof"}]},
                "changedFiles": ["blunder/core.py"],
            }
            record_outcome(root, mission, "accepted", 5, {"latencyBefore": 10, "latencyAfter": 6}, "proved")
            summary = outcome_summary(root, ("engineering:build-with-proof",))
        self.assertEqual(summary["averageScore"], 5.0)
        self.assertEqual(summary["skillSignals"]["engineering:build-with-proof"]["outcomes"], 1)

    def test_independent_reviewer_is_read_only_separate_and_can_reject(self) -> None:
        proposal = Proposal(
            "proof",
            (ProposedChange("blunder/core.py", "value = 2\n", "repair"),),
            ("tests",),
        )
        decision = SimpleNamespace(
            selected=SimpleNamespace(
                target_files=("blunder/core.py",),
                validation=("run tests",),
                rollback="restore snapshot",
            )
        )
        budget = ExecutionBudget(300, 3, 100000, 2.0, None)
        with mock.patch(
            "blunder.native_agent.provider_json",
            return_value={"verdict": "APPROVE", "confidence": "high", "findings": [], "requiredChecks": ["tests"]},
        ) as provider:
            review = request_review(
                "key",
                "review-model",
                "repair core",
                proposal,
                decision,
                {"blunder/core.py": "value = 1\n"},
                budget,
            )
        self.assertTrue(review["readOnly"])
        self.assertTrue(review["separateContext"])
        self.assertIn("workspaceEvidence", json.loads(provider.call_args.args[3]))
        with mock.patch(
            "blunder.native_agent.provider_json",
            return_value={"verdict": "REJECT", "confidence": "high", "findings": [{"claim": "unsafe"}], "requiredChecks": []},
        ), self.assertRaisesRegex(ValueError, "rejected"):
            request_review(
                "key",
                "review-model",
                "repair core",
                proposal,
                decision,
                {"blunder/core.py": "value = 1\n"},
                budget,
            )


if __name__ == "__main__":
    unittest.main()
