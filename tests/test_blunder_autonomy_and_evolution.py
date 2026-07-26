from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import subprocess
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_powershell(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def parse_last_json(stdout: str) -> dict[str, object]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    start_index = 0
    for index, line in enumerate(lines):
        if line.lstrip().startswith("{"):
            start_index = index
            break
    return json.loads("\n".join(lines[start_index:]))


class BlunderAutonomyAndEvolutionTests(unittest.TestCase):
    def test_idle_status_emits_complete_replenishment_state_without_prior_artifacts(self) -> None:
        with TemporaryDirectory() as temp_text:
            root = Path(temp_text)
            result = run_powershell(
                [
                    "-File",
                    "scripts/obtuse/idle-mode.ps1",
                    "-Action",
                    "Status",
                    "-QueuePath",
                    str(root / "queue.json"),
                    "-ArtifactRoot",
                    str(root / "runtime"),
                    "-ReplenishmentCooldownSeconds",
                    "45",
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            status = parse_last_json(result.stdout)
            replenishment = status["replenishmentState"]
        self.assertEqual(replenishment["policy"], "non-churning-v1")
        self.assertEqual(replenishment["cooldownSeconds"], 45)
        self.assertEqual(replenishment["cursor"], 0)

    def test_native_compound_planner_adapts_pressure_without_authorizing_mutation(self) -> None:
        with TemporaryDirectory() as temp_text:
            ledger_root = Path(temp_text) / "ledgers"
            ledger_root.mkdir(parents=True, exist_ok=True)
            patch_rows = [
                {
                    "runId": f"reject-{index}",
                    "candidateAccepted": False,
                    "patchApplied": False,
                    "patchType": "STATUS_OBSERVABILITY_COMMENT",
                    "result": "SKIPPED_THRESHOLD_PRECHECK_FAILED",
                }
                for index in range(4)
            ]
            patch_rows.append(
                {
                    "runId": "validation-elite",
                    "candidateAccepted": True,
                    "patchApplied": True,
                    "patchType": "VALIDATION_STABILIZER",
                    "result": "PASS",
                }
            )
            promotion_rows = [
                {
                    "runId": f"reject-{index}",
                    "promoted": False,
                    "result": "SKIPPED_THRESHOLD_PRECHECK_FAILED",
                }
                for index in range(4)
            ]
            promotion_rows.append(
                {
                    "runId": "validation-elite",
                    "promoted": True,
                    "result": "PROMOTED",
                }
            )
            promotion_rows.extend(
                {
                    "runId": f"stagnant-{index}",
                    "promoted": False,
                    "result": "REJECTED",
                }
                for index in range(4)
            )
            (ledger_root / "self_coding_patch_ledger.jsonl").write_text(
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in patch_rows),
                encoding="utf-8",
            )
            (ledger_root / "promotion_ledger.jsonl").write_text(
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in promotion_rows),
                encoding="utf-8",
            )
            plan_path = Path(temp_text) / "compound_evolution_plan.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/obtuse/blunder-compound-evolution-planner.py",
                    "--ledger-root",
                    str(ledger_root),
                    "--plan-path",
                    str(plan_path),
                ],
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(plan["status"], "PASS")
            self.assertGreater(plan["pressure"]["level"], 1.0)
            self.assertGreater(plan["pressure"]["candidateBudgetNext"], 1)
            self.assertIn("observability", plan["cooledDownFamilies"])
            self.assertEqual(plan["championFloor"]["candidate"], "validation-elite")
            self.assertEqual(plan["championFloor"]["fitness"], 1.0)
            self.assertTrue(plan["safety"]["advisoryOnly"])
            self.assertFalse(plan["safety"]["sourceMutationAuthorized"])
            self.assertFalse(plan["safety"]["thresholdReductionAuthorized"])
            self.assertTrue(plan_path.is_file())

    def test_approved_inflow_candidates_validate_without_queue_mutation(self) -> None:
        with TemporaryDirectory() as temp_text:
            queue_path = Path(temp_text) / "idle-task-queue.json"
            queue_path.write_text(json.dumps({"version": "1", "tasks": []}), encoding="utf-8")
            inflow_root = Path(temp_text) / "inflow"
            inflow_root.mkdir(parents=True, exist_ok=True)
            promotion_record = {
                "candidate_id": "candidate-validation-rule",
                "source_record_id": "source-record-1",
                "skill_token_id": "skill-token-1",
                "eligible": True,
                "active_skill_token": True,
                "promotion_status": "PROMOTED_AUTONOMOUS_THRESHOLD_PASS",
                "reason": "integration fixture",
                "rollback_path_required": True,
                "demotion_path_required": True,
            }
            (inflow_root / "promotion_bridge_ledger.jsonl").write_text(
                json.dumps(promotion_record, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report_root = Path(temp_text) / "wiring-report"
            result = run_powershell(
                [
                    "-File",
                    "scripts/obtuse/blunder-wire-approved-inflow-candidates.ps1",
                    "-Mode",
                    "Validate",
                    "-QueuePath",
                    str(queue_path),
                    "-InflowArtifactRoot",
                    str(inflow_root),
                    "-ReportRoot",
                    str(report_root),
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = parse_last_json(result.stdout)
            self.assertEqual(report["status"], "PASS")
            self.assertGreaterEqual(int(report["approvedCandidateCount"]), 1)
            self.assertEqual(int(report["addedTaskCount"]), 0)
            queue = json.loads(queue_path.read_text(encoding="utf-8-sig"))
            self.assertEqual(queue["tasks"], [])

if __name__ == "__main__":
    unittest.main()
