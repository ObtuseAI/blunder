from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from blunder.autonomy_lane import SelfCodingBudget, SelfCodingRequest, self_coding_preflight
from blunder.cognitive_events import load_decision_events
from blunder.discovery_engine import build_discovery_brief
from blunder.evaluation_arena import arena_report, load_suite, parse_measurement
from blunder.intent_guard import intent_diff_firewall
from blunder.language_intelligence import completions, references, rename_plan, structural_diff
from blunder.mission_campaign import program_canary_report
from blunder.native_agent import ProposedChange
from blunder.release_governance import build_release_manifest, verify_release_manifest
from blunder.reliability_campaign import ReliabilityLimits, campaign_report


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_git(root: Path, arguments: tuple[str, ...]) -> None:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Git fixture failed: {completed.stderr}")


def completed_specialist(identifier: str, path: str) -> dict[str, object]:
    return {
        "id": identifier,
        "status": "completed",
        "integrationState": "READY_TO_APPLY",
        "claimedPaths": [path],
        "budget": {"seconds": 900, "providerCalls": 3, "tokens": 60_000, "costUsd": 0.25},
        "worker": {
            "report": {
                "budget": {"elapsedSeconds": 20, "providerCalls": 3, "totalTokens": 10_000, "costUsd": 0.05}
            }
        },
    }


class BlunderP13P20IntegrationTests(unittest.TestCase):
    def test_p13_intent_firewall_rejects_adjacent_collateral_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("P9 alpha\nP10 beta\n", encoding="utf-8")
            collateral = ProposedChange("README.md", "P9 alpha's\nP10 better beta\n", "Improve P10")
            intended = ProposedChange("README.md", "P9 alpha\nP10 better beta\n", "Improve P10")
            self.assertEqual(intent_diff_firewall(root, "Only improve P10 beta", (collateral,))["verdict"], "REJECT")
            self.assertEqual(intent_diff_firewall(root, "Only improve P10 beta", (intended,))["verdict"], "APPROVE")

    def test_p14_arena_detects_false_accept_and_refuses_fake_baselines(self) -> None:
        cases = load_suite(REPO_ROOT / "configs" / "obtuse" / "blunder-evaluation-suite.json")
        measurement = parse_measurement(
            {
                "caseId": "intent-collateral-001",
                "candidateId": "blunder-canary",
                "patch": "repository fingerprint request digest",
                "changedPaths": ["README.md"],
                "validationPassed": True,
                "reviewerVerdict": "APPROVE",
                "courtVerdict": "APPROVE",
                "terminalStatus": "completed",
                "operatorStatus": "accepted",
                "elapsedSeconds": 10,
                "totalTokens": 1000,
                "costUsd": 0.01,
            }
        )
        report = arena_report(cases, (measurement,), ())
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["falseAcceptRate"], 1.0)
        self.assertEqual(report["baselines"], [])
        with self.assertRaisesRegex(ValueError, "requires source"):
            arena_report(cases, (), ({"candidateId": "unproven"},))

    def test_p15_program_canary_requires_disjoint_ready_specialists_and_synthesis(self) -> None:
        specialists = [completed_specialist("a", "blunder/a.py"), completed_specialist("b", "blunder/b.py")]
        synthesis = {
            "id": "s",
            "status": "completed",
            "integrationState": "READY_TO_APPLY",
            "worker": {"report": {"status": "SYNTHESIZED_AND_VALIDATED"}},
        }
        program = {"id": "p", "status": "completed", "specialists": specialists, "synthesis": synthesis}
        self.assertEqual(program_canary_report(program)["status"], "PASS")
        specialists[1]["claimedPaths"] = ["blunder/a.py"]
        self.assertEqual(program_canary_report(program)["status"], "FAIL")

    def test_p16_self_coding_requires_explicit_authority_and_preserves_denials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "blunder").mkdir()
            (root / "blunder" / "target.py").write_text("value = 1\n", encoding="utf-8")
            run_git(root, ("init", "-q"))
            authorization = {
                "schema": "obtuse.blunder.operator_autonomy_authorization.v1",
                "enabled": False,
                "authorizationId": "operator-proof",
                "operatorStatement": "Authorize one bounded isolated mission.",
                "approvedScopes": ["bounded_self_coding"],
                "expiresAt": "2099-01-01T00:00:00Z",
                "preserveHardDenials": [
                    "windows_permission_or_acl_bypass",
                    "secret_printing",
                    "live_benchmark_launch",
                    "destructive_filesystem_cleanup",
                    "git_push",
                    "safety_boundary_weakening",
                    "production_deploy",
                    "public_submission",
                ],
            }
            request = SelfCodingRequest(
                "Improve the typed target safely",
                ("blunder/target.py",),
                SelfCodingBudget(900, 3, 60_000, 0.25),
            )
            blocked = self_coding_preflight(root, authorization, request, False)
            self.assertEqual(blocked["status"], "BLOCKED")
            authorization["enabled"] = True
            authorized = self_coding_preflight(root, authorization, request, False)
            self.assertEqual(authorized["status"], "AUTHORIZED_TO_LAUNCH_ISOLATED_MISSION")
            self.assertFalse(authorized["integrationAuthorized"])
            self.assertFalse(authorized["pushAuthorized"])
            self.assertEqual(authorized["constitutionalShadow"]["parity"], "MATCH_ALLOW")
            denied = self_coding_preflight(
                root,
                authorization,
                SelfCodingRequest(
                    "Push the change to the remote repository",
                    ("blunder/target.py",),
                    SelfCodingBudget(900, 3, 60_000, 0.25),
                ),
                False,
            )
            self.assertEqual(denied["status"], "BLOCKED")
            self.assertEqual(denied["constitutionalShadow"]["status"], "DENY")
            self.assertEqual(denied["constitutionalShadow"]["parity"], "MATCH_DENY")
            self.assertIn("REQUESTED_IMMUTABLE_DENIAL", denied["constitutionalShadow"]["reasonCodes"])
            self.assertEqual(load_decision_events(root)[-1]["effect"], "self-coding.launch")

    def test_p17_language_intelligence_is_repository_aware_and_preview_only(self) -> None:
        observed = references(REPO_ROOT, "intent_diff_firewall", 50)
        suggestions = completions(REPO_ROOT, "intent", 20)
        rename = rename_plan(REPO_ROOT, "intent_diff_firewall", "intent_change_firewall", 50)
        difference = structural_diff("example.py", "def old() -> None:\n    pass\n", "def new() -> None:\n    pass\n")
        self.assertGreaterEqual(observed["count"], 2)
        self.assertIn("intent_diff_firewall", [item["label"] for item in suggestions["items"]])
        self.assertFalse(rename["applied"])
        self.assertEqual(difference["symbolsAdded"], ["new"])

    def test_p18_discovery_brief_carries_exact_local_provenance(self) -> None:
        report = build_discovery_brief(REPO_ROOT, "How should Blunder prevent collateral source edits?", 6)
        self.assertEqual(report["status"], "READY_FOR_EXPERIMENT")
        self.assertEqual(report["providerCalls"], 0)
        self.assertGreaterEqual(report["evidenceCount"], 1)
        self.assertEqual(report["hypothesisCount"], 4)
        self.assertTrue(all(item["path"] and item["sha256"] for item in report["evidence"]))

    def test_p19_reliability_campaign_exercises_live_contract_and_faults(self) -> None:
        snapshot = {
            "active": True,
            "pidValid": True,
            "heartbeatAgeSeconds": 1,
            "queueStats": {"failed": 0, "blocked": 0, "quarantined": 0},
            "finalRecommendedOperatorAction": "NO_ACTION_IDLE_HEALTHY",
            "rawRunnerStatus": {"state": {"cyclesRun": 800}},
        }
        limits = ReliabilityLimits(120, 0, 0, 0, 1.0)
        report = campaign_report((snapshot, snapshot), limits, "start", "finish")
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["healthyRate"], 1.0)
        self.assertTrue(all(item["pass"] for item in report["faultDrills"]))

    def test_p20_release_manifest_binds_exact_private_commit_and_signature(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text('[project]\nname = "blunder-fixture"\nversion = "2.0.0"\n', encoding="utf-8")
            (root / "contract.json").write_text('{"schema":"obtuse.blunder.fixture.v1"}\n', encoding="utf-8")
            run_git(root, ("init", "-q"))
            run_git(root, ("config", "user.email", "proof@blunder.local"))
            run_git(root, ("config", "user.name", "Blunder Proof"))
            run_git(root, ("add", "."))
            run_git(root, ("commit", "-qm", "fixture"))
            head = subprocess.check_output(("git", "-C", str(root), "rev-parse", "HEAD"), text=True).strip()
            key = "first-party-release-proof-key-32-chars"
            manifest = build_release_manifest(
                root,
                {"status": "READY_FOR_PRIVATE_GITHUB_REVIEW", "visibility": {"private": True}},
                {"status": "PASS", "commit": head, "sourceTreeSha256": "ABC"},
                ({"name": "release-proof", "conclusion": "SUCCESS"},),
                key,
            )
            self.assertEqual(manifest["status"], "READY_FOR_EXACT_OPERATOR_CONFIRMATION")
            self.assertEqual(verify_release_manifest(manifest, key)["status"], "PASS")
            manifest["version"] = "2.0.1"
            self.assertEqual(verify_release_manifest(manifest, key)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
