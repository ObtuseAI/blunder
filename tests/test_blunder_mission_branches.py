from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from blunder.capabilities import load_registry
from blunder.cognitive_events import load_decision_events
from blunder.missions.branches import (
    MISSION_RECORD_SCHEMA,
    MissionBranchConfig,
    MissionBranchManager,
    copy_review_state,
    finalize_mission_patch,
    load_mission_config,
    run_git,
)


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


class BlunderMissionBranchTests(unittest.TestCase):
    def initialize_repository(self, root: Path) -> None:
        git(root, "init", "-b", "main")
        git(root, "config", "user.email", "blunder@example.invalid")
        git(root, "config", "user.name", "Blunder Proof")
        (root / "blunder").mkdir()
        (root / "blunder" / "engine.py").write_text("state = 'committed'\n", encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-m", "seed")

    def test_review_state_is_seeded_without_committing_and_patch_is_mission_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            repository = base / "repository"
            worktree = base / "worktrees" / "mission"
            mission_path = base / "state" / "mission"
            repository.mkdir()
            mission_path.mkdir(parents=True)
            self.initialize_repository(repository)
            (repository / "blunder" / "engine.py").write_text("state = 'review'\n", encoding="utf-8")
            (repository / "blunder" / "review.py").write_text("review = True\n", encoding="utf-8")
            run_git(repository, ("worktree", "add", "-b", "codex/mission-proof", str(worktree), "HEAD"), None, True)
            seed = copy_review_state(repository, worktree)
            self.assertEqual((worktree / "blunder" / "engine.py").read_text(encoding="utf-8"), "state = 'review'\n")
            self.assertTrue((worktree / "blunder" / "review.py").is_file())
            self.assertEqual(git(worktree, "diff", "--name-only"), "")
            self.assertIn("blunder/engine.py", git(worktree, "diff", "--cached", "--name-only"))
            self.assertGreater(int(seed["trackedPatchBytes"]), 0)

            (worktree / "blunder" / "engine.py").write_text("state = 'mission'\n", encoding="utf-8")
            (worktree / "blunder" / "new_capability.py").write_text("elite = True\n", encoding="utf-8")
            record = {
                "schema": MISSION_RECORD_SCHEMA,
                "id": "mission",
                "worktree": str(worktree),
            }
            (mission_path / "record.json").write_text(json.dumps(record), encoding="utf-8")
            result = finalize_mission_patch(
                MissionBranchConfig(repository, base / "worktrees", 3, 1800, 3, 200000, 5.0, 14),
                mission_path,
            )
            patch = (mission_path / "mission-only.patch").read_text(encoding="utf-8")
            self.assertEqual(
                result["changedFiles"],
                ["blunder/engine.py", "blunder/new_capability.py"],
            )
            self.assertNotIn("state = 'committed'", patch)
            self.assertIn("state = 'mission'", patch)
            self.assertIn("elite = True", patch)
            self.assertEqual(result["integrationState"], "READY_TO_APPLY")

            (repository / "blunder" / "engine.py").write_text("state = 'operator-drift'\n", encoding="utf-8")
            drifted = finalize_mission_patch(
                MissionBranchConfig(repository, base / "worktrees", 3, 1800, 3, 200000, 5.0, 14),
                mission_path,
            )
            self.assertEqual(drifted["integrationState"], "PRIMARY_DRIFTED")

    def test_configuration_requires_external_worktree_root_and_caps_parallelism(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            runtime = base / "runtime"
            repository = base / "repository"
            workspace = base / "worktrees"
            runtime.mkdir()
            repository.mkdir()
            self.initialize_repository(repository)
            config_path = runtime / "artifacts" / "obtuse" / "blunder-shell-v2" / "mission-branches.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "schema": "obtuse.blunder.mission-branch-config.v1",
                        "repositoryRoot": str(repository),
                        "workspaceRoot": str(workspace),
                        "maxParallel": 3,
                    }
                ),
                encoding="utf-8",
            )
            config, reason = load_mission_config(runtime)
            self.assertIsNone(reason)
            self.assertIsNotNone(config)
            assert config is not None
            self.assertEqual(config.max_parallel, 3)
            self.assertEqual(config.repository_root, repository.resolve())

    def test_manager_runs_worker_in_real_branch_and_emits_drift_checked_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            runtime = base / "runtime"
            repository = base / "repository"
            workspace = base / "worktrees"
            runtime.mkdir()
            repository.mkdir()
            self.initialize_repository(repository)
            config_path = runtime / "artifacts" / "obtuse" / "blunder-shell-v2" / "mission-branches.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "schema": "obtuse.blunder.mission-branch-config.v1",
                        "repositoryRoot": str(repository),
                        "workspaceRoot": str(workspace),
                        "maxParallel": 3,
                    }
                ),
                encoding="utf-8",
            )
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
Path(args.root, 'blunder', 'engine.py').write_text("state = 'mission-worker'\\n", encoding='utf-8')
Path(args.result).write_text(json.dumps({'ok': True, 'report': {'status': 'APPLIED_AND_VALIDATED'}}), encoding='utf-8')
""",
                encoding="utf-8",
            )
            manager = MissionBranchManager(runtime, load_registry(REPO_ROOT), sys.executable)
            started = manager.start("Implement an independently reviewable engine feature", True)
            self.assertEqual(started["status"], "running")
            deadline = time.monotonic() + 10
            terminal: dict[str, object] | None = None
            while time.monotonic() < deadline:
                current = manager.get(str(started["id"]))
                if str(current["status"]) in {"completed", "failed", "interrupted"}:
                    terminal = current
                    break
                time.sleep(0.05)
            self.assertIsNotNone(terminal)
            assert terminal is not None
            self.assertEqual(terminal["status"], "completed")
            self.assertEqual(terminal["integrationState"], "READY_TO_APPLY")
            self.assertEqual(terminal["changedFiles"], ["blunder/engine.py"])
            self.assertTrue(Path(str(terminal["worktree"])).is_dir())
            self.assertIn(str(terminal["branch"]), git(repository, "branch", "--list"))
            with self.assertRaisesRegex(ValueError, "exact confirmation"):
                manager.integrate(str(terminal["id"]), "INTEGRATE")
            denied_event = load_decision_events(runtime)[-1]
            self.assertEqual(denied_event["effect"], "mission.integrate")
            self.assertEqual(denied_event["parity"], "MATCH_DENY")
            self.assertEqual(denied_event["decision"]["status"], "DENY")
            integrated = manager.integrate(
                str(terminal["id"]),
                f"INTEGRATE {terminal['id']}",
            )
            self.assertEqual(integrated["integrationState"], "APPLIED_TO_REVIEW_TREE")
            self.assertEqual(integrated["constitutionalShadow"]["integration"]["parity"], "MATCH_ALLOW")
            self.assertEqual(
                (repository / "blunder" / "engine.py").read_text(encoding="utf-8"),
                "state = 'mission-worker'\n",
            )
            with self.assertRaisesRegex(ValueError, "exact confirmation"):
                manager.cleanup(str(terminal["id"]), "CLEAN")
            cleaned = manager.cleanup(str(terminal["id"]), f"CLEAN {terminal['id']}")
            self.assertTrue(cleaned["worktreeRemoved"])
            self.assertFalse(Path(str(terminal["worktree"])).exists())

    def test_manager_runs_three_isolated_workers_and_refuses_a_fourth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            runtime = base / "runtime"
            repository = base / "repository"
            workspace = base / "worktrees"
            runtime.mkdir()
            repository.mkdir()
            self.initialize_repository(repository)
            config_path = runtime / "artifacts" / "obtuse" / "blunder-shell-v2" / "mission-branches.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "schema": "obtuse.blunder.mission-branch-config.v1",
                        "repositoryRoot": str(repository),
                        "workspaceRoot": str(workspace),
                        "maxParallel": 3,
                    }
                ),
                encoding="utf-8",
            )
            worker = runtime / "scripts" / "obtuse" / "blunder_mission_worker.py"
            worker.parent.mkdir(parents=True)
            worker.write_text(
                """from __future__ import annotations
import argparse
import time
parser = argparse.ArgumentParser()
parser.add_argument('--root', required=True)
parser.add_argument('--task', required=True)
parser.add_argument('--result', required=True)
parser.add_argument('--provider-approved', action='store_true')
parser.add_argument('--identity-token', required=True)
parser.parse_args()
time.sleep(30)
""",
                encoding="utf-8",
            )
            manager = MissionBranchManager(runtime, load_registry(REPO_ROOT), sys.executable)
            missions = [
                manager.start(f"Implement isolated capability number {index}", True)
                for index in range(1, 4)
            ]
            state = manager.state()
            self.assertEqual(state["capacity"], {"active": 3, "maximum": 3, "available": 0})
            self.assertEqual(len({str(item["pid"]) for item in missions}), 3)
            self.assertEqual(len({str(item["worktree"]) for item in missions}), 3)
            with self.assertRaisesRegex(ValueError, "capacity is full"):
                manager.start("Implement isolated capability number four", True)
            for mission in missions:
                cancelled = manager.cancel(str(mission["id"]))
                self.assertEqual(cancelled["status"], "cancelled")

    def test_reconciliation_three_way_merges_non_overlapping_primary_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            runtime = base / "runtime"
            repository = base / "repository"
            workspace = base / "worktrees"
            runtime.mkdir()
            repository.mkdir()
            self.initialize_repository(repository)
            git(repository, "config", "core.autocrlf", "false")
            engine = repository / "blunder" / "engine.py"
            engine.write_bytes(b"state = 'committed'\nanchor_one = 1\nanchor_two = 2\nanchor_three = 3\nsecondary = 'base'\n")
            git(repository, "add", "-A")
            git(repository, "commit", "-m", "two-line-base")
            config_path = runtime / "artifacts" / "obtuse" / "blunder-shell-v2" / "mission-branches.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "schema": "obtuse.blunder.mission-branch-config.v1",
                        "repositoryRoot": str(repository),
                        "workspaceRoot": str(workspace),
                        "maxParallel": 3,
                    }
                ),
                encoding="utf-8",
            )
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
Path(args.root, 'blunder', 'engine.py').write_bytes(b"state = 'mission'\\nanchor_one = 1\\nanchor_two = 2\\nanchor_three = 3\\nsecondary = 'base'\\n")
Path(args.result).write_text(json.dumps({'ok': True, 'report': {'status': 'APPLIED_AND_VALIDATED'}}), encoding='utf-8')
""",
                encoding="utf-8",
            )
            manager = MissionBranchManager(runtime, load_registry(REPO_ROOT), sys.executable)
            started = manager.start("Implement an independently reviewable engine feature", True)
            deadline = time.monotonic() + 10
            terminal: dict[str, object] | None = None
            while time.monotonic() < deadline:
                current = manager.get(str(started["id"]))
                if current["status"] == "completed":
                    terminal = current
                    break
                time.sleep(0.05)
            self.assertIsNotNone(terminal)
            engine.write_bytes(b"state = 'committed'\nanchor_one = 1\nanchor_two = 2\nanchor_three = 3\nsecondary = 'operator'\n")
            assert terminal is not None
            reconciled = manager.reconcile(
                str(terminal["id"]),
                f"RECONCILE {terminal['id']}",
            )
            self.assertEqual(reconciled["integrationState"], "READY_TO_APPLY")
            merged = Path(str(reconciled["worktree"]), "blunder", "engine.py").read_text(encoding="utf-8")
            self.assertIn("state = 'mission'", merged)
            self.assertIn("secondary = 'operator'", merged)


if __name__ == "__main__":
    unittest.main()
