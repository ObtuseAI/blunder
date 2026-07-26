from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import subprocess
import unittest

from blunder.inflow.agent_trace_miner import mine_agent_trace
from blunder.inflow.contradiction_detector import detect_record_contradictions
from blunder.inflow.inflow_ranker import compute_priority
from blunder.inflow.inflow_scheduler import run_scheduler
from blunder.inflow.license_gate import classify_license, is_license_compatible
from blunder.inflow.models import SourceCandidate, build_record
from blunder.inflow.quarantine_gate import quarantine_source
from blunder.inflow.replay_fixture_builder import build_replay_fixtures
from blunder.inflow.secret_sentinel import scan_text_for_risk
from blunder.inflow.source_trust_scorer import compute_trust_score
from blunder.inflow.synthetic_drill_generator import generate_synthetic_drills
from blunder.inflow.visual_evidence_ingestor import ingest_visual_evidence


def write_fixture_repo(root: Path) -> None:
    paths = {
        "artifacts/obtuse/blunder-full-autonomy-uncorked-operations-v1/FINAL_REPORT.json": {
            "status": "PASS",
            "terminal": "BLUNDER_FULL_AUTONOMY_UNCORKED_OPERATIONAL",
            "proof": "proof ledger pass",
        },
        "artifacts/obtuse/blunder-full-autonomy-uncorked-operations-v1/final_validation_summary.json": {
            "standard": "PASS",
            "dunce": "PASS",
            "benchmark": "REPORT_ONLY_PASS",
        },
        "artifacts/obtuse/blunder-full-autonomy-uncorked-operations-v1/autonomy_bootstrap_summary.json": {
            "heartbeat": "stopped",
            "orphanProcess": False,
            "provider": "no escalation",
        },
        "artifacts/obtuse/blunder-dunce-import-and-benchmark-appliance-mode-v1/FINAL_REPORT.json": {
            "dunce": "proof regression pass",
            "benchmark": "not launched",
        },
        "artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-events.log": "heartbeat observed\nprovider failure telemetry\n",
        "scripts/obtuse/blunder.ps1": "function Invoke-BlunderStatus { return 'PASS' }\n",
    }
    for relative_path, payload in paths.items():
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, str):
            target.write_text(payload, encoding="utf-8")
        else:
            target.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def inline_candidate(text: str, source_uri: str) -> SourceCandidate:
    return {
        "source_uri": source_uri,
        "source_type": "agent_trace",
        "trust_class": "AGENT_TRACE",
        "license_class": "OWNED",
        "inline_text": text,
        "authority_score": 0.5,
        "relevance_score": 0.7,
        "freshness_score": 0.8,
        "reproducibility_score": 0.6,
        "validation_history_score": 0.5,
        "replayability_score": 0.65,
        "internal_alignment_score": 0.7,
    }


class BlunderRecursiveDataInflowMeshV2Tests(unittest.TestCase):
    def test_run_once_writes_required_artifacts_and_blocks_external_effects(self) -> None:
        with TemporaryDirectory() as repo_text, TemporaryDirectory() as artifact_text:
            repo_root = Path(repo_text)
            artifact_root = Path(artifact_text) / "artifacts" / "blunder" / "inflow"
            write_fixture_repo(repo_root)
            result = run_scheduler(repo_root, artifact_root, "RunOnce")
            expected = [
                "source_registry.json",
                "quarantine_ledger.jsonl",
                "rejected_sources.jsonl",
                "inflow_records.jsonl",
                "extraction_ledger.jsonl",
                "capability_candidates.jsonl",
                "replay_fixtures.jsonl",
                "synthetic_drills.jsonl",
                "source_trust_scores.jsonl",
                "priority_scores.jsonl",
                "contradictions.jsonl",
                "recursive_feedback_ledger.jsonl",
                "promotion_bridge_ledger.jsonl",
                "inflow_scoreboard.json",
                "validation_summary.json",
                "final_report.json",
            ]
            for name in expected:
                self.assertTrue((artifact_root / name).exists(), name)
            scoreboard = result["scoreboard"]
            self.assertFalse(scoreboard["live_benchmark_launched"])
            self.assertFalse(scoreboard["production_mutation"])
            self.assertFalse(scoreboard["safety_boundary_weakened"])
            self.assertFalse(scoreboard["external_dependency_introduced"])
            self.assertGreater(scoreboard["capability_candidate_count"], 0)
            self.assertGreater(scoreboard["replay_fixture_count"], 0)

    def test_audit_only_does_not_mutate_artifacts(self) -> None:
        with TemporaryDirectory() as repo_text, TemporaryDirectory() as artifact_text:
            repo_root = Path(repo_text)
            artifact_root = Path(artifact_text) / "audit" / "must_not_exist"
            write_fixture_repo(repo_root)
            result = run_scheduler(repo_root, artifact_root, "AuditOnly")
            self.assertTrue(result["validation_summary"]["pass"])
            self.assertFalse(artifact_root.exists())

    def test_blocked_secret_env_api_key_private_repo_and_leaked_code_rejection(self) -> None:
        with TemporaryDirectory() as artifact_text:
            artifact_root = Path(artifact_text)
            known_hashes: set[str] = set()
            risky_texts = [
                "OPENROUTER_API_KEY='PLACEHOLDER_OPENROUTER_API_KEY'",
                "api_key='PLACEHOLDER_API_KEY_VALUE'",
                "private repo marker",
                "leaked code marker",
            ]
            for index, text in enumerate(risky_texts):
                record, rejection = quarantine_source(artifact_root, inline_candidate(text, f"agent://risk-{index}"), known_hashes, True)
                self.assertEqual(record["promotion_status"], "rejected")
                self.assertIsNotNone(rejection)
            env_path = artifact_root / ".env"
            env_path.write_text("SAFE_NAME=value", encoding="utf-8")
            candidate = inline_candidate("", "file://env")
            candidate["path"] = str(env_path)
            record, rejection = quarantine_source(artifact_root, candidate, known_hashes, True)
            self.assertIn("ENV_FILE_BLOCKED", record["risk_flags"])
            self.assertIsNotNone(rejection)

    def test_duplicate_detection_license_scoring_priority_and_stale_penalty(self) -> None:
        with TemporaryDirectory() as artifact_text:
            artifact_root = Path(artifact_text)
            known_hashes: set[str] = set()
            first, _ = quarantine_source(artifact_root, inline_candidate("same capability proof", "agent://one"), known_hashes, True)
            second, _ = quarantine_source(artifact_root, inline_candidate("same capability proof", "agent://two"), known_hashes, True)
            self.assertEqual(first["duplication_flags"], [])
            self.assertIn("DUPLICATE_CONTENT_HASH", second["duplication_flags"])
            self.assertEqual(classify_license("proprietary confidential", "MIT"), "INCOMPATIBLE")
            self.assertFalse(is_license_compatible("INCOMPATIBLE"))
            stale = inline_candidate("proof", "agent://stale")
            stale["freshness_score"] = 0.1
            self.assertLess(compute_trust_score(stale, [], 0), compute_trust_score(inline_candidate("proof", "agent://fresh"), [], 0))
            self.assertGreater(compute_priority(first), compute_priority(second))

    def test_paopvol_contradiction_agent_trace_and_replay_fixture_gates(self) -> None:
        record = build_record(inline_candidate("PASS then FAIL because bad assumption fixed and validated", "agent://trace"), "hash", 1.0, 0.0, "", "", "", "", "", [], [], "quarantined")
        record["normalized_text_path"] = ""
        record["capability_candidates"] = mine_agent_trace(record, "bad assumption fixed and validated")
        record["paopvol"]["problem"] = "Agent trace is workflow evidence, not truth."
        self.assertTrue(record["capability_candidates"])
        self.assertEqual(record["paopvol"]["problem"], "Agent trace is workflow evidence, not truth.")
        with TemporaryDirectory() as artifact_text:
            text_path = Path(artifact_text) / "normalized.txt"
            text_path.write_text("PASS and FAIL contradiction", encoding="utf-8")
            record["normalized_text_path"] = str(text_path)
            record = detect_record_contradictions(record)
            self.assertIn("PASS_FAIL_MIXED_CLAIM", record["contradiction_flags"])
            record = build_replay_fixtures(record)
            self.assertTrue(record["replay_fixtures"])
            self.assertFalse(any(fixture["live_benchmark_launch"] for fixture in record["replay_fixtures"]))

    def test_visual_evidence_record_parser_loss_and_synthetic_drill_generation(self) -> None:
        with TemporaryDirectory() as artifact_text:
            root = Path(artifact_text)
            image = root / "terminal.png"
            image.write_bytes(b"not-real-image-but-controlled-fixture")
            candidate = inline_candidate("", "file://terminal.png")
            candidate["path"] = str(image)
            record, rejection = quarantine_source(root / "artifacts", candidate, set(), True)
            self.assertIsNone(rejection)
            record = ingest_visual_evidence(root / "artifacts", record, True)
            self.assertTrue(record["visual_tiles_path"])
            tiles = json.loads(Path(record["visual_tiles_path"]).read_text(encoding="utf-8"))
            self.assertIn("SCREENSHOT_ONLY_EVIDENCE", tiles["tiles"][0]["labels"])
            record["capability_candidates"] = [{"candidate_id": "failure", "target_type": "FailureMode"}]
            drills = generate_synthetic_drills([record])
            self.assertTrue(drills)
            self.assertFalse(drills[0]["launches_live_benchmark"])

    def test_scheduler_modes_never_promote_raw_or_launch_benchmark(self) -> None:
        with TemporaryDirectory() as repo_text, TemporaryDirectory() as artifact_text:
            repo_root = Path(repo_text)
            artifact_root = Path(artifact_text) / "mesh"
            write_fixture_repo(repo_root)
            for mode in ["IngestOnly", "ExtractOnly", "ReplayOnly", "PromoteEligibleOnly", "IdleAutonomySafeLoop"]:
                result = run_scheduler(repo_root, artifact_root / mode, mode)  # type: ignore[arg-type]
                scoreboard = result["scoreboard"]
                self.assertFalse(scoreboard["raw_inflow_direct_promotion"], mode)
                self.assertFalse(scoreboard["live_benchmark_launched"], mode)
                self.assertFalse(scoreboard["production_mutation"], mode)
                self.assertFalse(scoreboard["safety_boundary_weakened"], mode)

    def test_secret_scanner_marker_coverage(self) -> None:
        flags = scan_text_for_risk("malware exploit chain private repo leaked code api_key='PLACEHOLDER_API_KEY_VALUE'")
        self.assertIn("MALWARE_MARKER", flags)
        self.assertIn("EXPLOIT_CHAIN_MARKER", flags)
        self.assertIn("PRIVATE_REPO_MARKER", flags)
        self.assertIn("LEAKED_CODE_MARKER", flags)
        self.assertIn("API_KEY_ASSIGNMENT", flags)

    def test_idle_autonomy_executes_inflow_mesh_task_through_idle_runner(self) -> None:
        with TemporaryDirectory() as temp_text:
            repo_root = Path.cwd()
            temp_root = Path(temp_text)
            queue_path = temp_root / "idle-task-queue.json"
            idle_artifact_root = temp_root / "idle-artifacts"
            mesh_artifact_root = temp_root / "mesh-artifacts"
            queue_path.write_text(
                json.dumps(
                    {
                        "version": "1",
                        "tasks": [
                            {
                                "id": "meshidleproof00000000000000000001",
                                "title": "Run Blunder inflow mesh proof pass",
                                "description": "Idle-owned backend inflow task; local proof pass only.",
                                "status": "pending",
                                "priority": 9,
                                "risk": 1,
                                "sourceProject": "obtuse/blunder",
                                "nextAction": "Run local inflow mesh and write idle bridge proof.",
                                "payload": {
                                    "actionType": "blunder-inflow-mesh",
                                    "mode": "IdleAutonomySafeLoop",
                                    "artifactRoot": str(mesh_artifact_root),
                                    "suppressFollowUp": True,
                                },
                                "simulationResult": None,
                                "executionResult": None,
                                "validationResult": None,
                                "createdAt": "2026-06-29T00:00:00Z",
                                "updatedAt": "2026-06-29T00:00:00Z",
                            }
                        ],
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(repo_root / "scripts" / "obtuse" / "idle-mode.ps1"),
                    "-Action",
                    "RunOnce",
                    "-QueuePath",
                    str(queue_path),
                    "-ArtifactRoot",
                    str(idle_artifact_root),
                    "-DisableReplenishment",
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            bridge_ledger = idle_artifact_root / "inflow-mesh-bridge-ledger.jsonl"
            self.assertTrue(bridge_ledger.exists())
            bridge_record = json.loads(bridge_ledger.read_text(encoding="utf-8-sig").strip().splitlines()[-1])
            self.assertTrue(bridge_record["validationPass"])
            self.assertEqual(bridge_record["terminalTarget"], "BLUNDER_RECURSIVE_DATA_INFLOW_MESH_V2_READY_WITH_PROOF")
            queue = json.loads(queue_path.read_text(encoding="utf-8-sig"))
            self.assertEqual(queue["tasks"][0]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
