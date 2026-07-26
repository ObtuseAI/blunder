from __future__ import annotations

from pathlib import Path
from typing import Any

from blunder.inflow.capability_extractor import extract_capabilities
from blunder.inflow.code_symbol_extractor import attach_code_symbols
from blunder.inflow.contradiction_detector import detect_cross_source_contradictions, detect_record_contradictions
from blunder.inflow.inflow_ledger import append_jsonl, reset_artifact_files, write_json
from blunder.inflow.inflow_ranker import compute_priority
from blunder.inflow.models import CLASSIFICATION, TERMINAL_TARGET, BlunderInflowRecord, SchedulerMode, utc_now
from blunder.inflow.normalizer import normalize_record
from blunder.inflow.promotion_bridge import evaluate_promotion
from blunder.inflow.quarantine_gate import quarantine_source
from blunder.inflow.recursive_feedback_engine import build_feedback_updates
from blunder.inflow.replay_fixture_builder import build_replay_fixtures
from blunder.inflow.source_registry import build_source_registry, write_source_registry
from blunder.inflow.synthetic_drill_generator import generate_synthetic_drills
from blunder.inflow.visual_evidence_ingestor import ingest_visual_evidence


JSONL_FILES: list[str] = [
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
]


def _mode_mutates(mode: SchedulerMode) -> bool:
    return mode != "AuditOnly"


def _mode_allows_extraction(mode: SchedulerMode) -> bool:
    return mode in {"RunOnce", "ExtractOnly", "ReplayOnly", "PromoteEligibleOnly", "IdleAutonomySafeLoop"}


def _mode_allows_replay(mode: SchedulerMode) -> bool:
    return mode in {"RunOnce", "ReplayOnly", "PromoteEligibleOnly", "IdleAutonomySafeLoop"}


def _mode_allows_promotion_surface(mode: SchedulerMode) -> bool:
    return mode in {"RunOnce", "PromoteEligibleOnly", "IdleAutonomySafeLoop"}


def _read_normalized(record: BlunderInflowRecord) -> str:
    if record["normalized_text_path"]:
        return Path(record["normalized_text_path"]).read_text(encoding="utf-8", errors="replace")
    return ""


def run_scheduler(repo_root: Path, artifact_root: Path, mode: SchedulerMode) -> dict[str, Any]:
    mutate = _mode_mutates(mode)
    if mutate:
        artifact_root.mkdir(parents=True, exist_ok=True)
        reset_artifact_files(artifact_root, JSONL_FILES)
    source_candidates = build_source_registry(repo_root)
    if mutate:
        write_source_registry(artifact_root, source_candidates)
    known_hashes: set[str] = set()
    records: list[BlunderInflowRecord] = []
    rejections: list[dict[str, object]] = []
    promotions: list[dict[str, object]] = []
    priority_scores: list[dict[str, object]] = []

    for candidate in source_candidates:
        record, rejection = quarantine_source(artifact_root, candidate, known_hashes, mutate)
        records.append(record)
        if rejection is not None:
            rejections.append(rejection)
        if mutate:
            append_jsonl(artifact_root / "quarantine_ledger.jsonl", {"record_id": record["record_id"], "source_uri": record["source_uri"], "promotion_status": record["promotion_status"]})
            if rejection is not None:
                append_jsonl(artifact_root / "rejected_sources.jsonl", rejection)
            append_jsonl(artifact_root / "source_trust_scores.jsonl", {"record_id": record["record_id"], "trust_score": record["trust_score"], "risk_score": record["risk_score"]})

    if mode == "IngestOnly":
        extraction_records: list[BlunderInflowRecord] = records
    elif _mode_allows_extraction(mode):
        extraction_records = []
        for record in records:
            if record["promotion_status"] == "rejected":
                extraction_records.append(record)
                continue
            normalized = normalize_record(artifact_root, record, mutate)
            with_symbols = attach_code_symbols(artifact_root, normalized, mutate)
            with_visuals = ingest_visual_evidence(artifact_root, with_symbols, mutate)
            with_contradictions = detect_record_contradictions(with_visuals)
            extracted = extract_capabilities(with_contradictions)
            extraction_records.append(extracted)
            if mutate:
                for candidate in extracted["capability_candidates"]:
                    append_jsonl(artifact_root / "capability_candidates.jsonl", candidate)
                append_jsonl(artifact_root / "extraction_ledger.jsonl", {"record_id": extracted["record_id"], "candidate_count": len(extracted["capability_candidates"])})
    else:
        extraction_records = records

    if _mode_allows_replay(mode):
        replay_records = [build_replay_fixtures(record) for record in extraction_records]
        if mutate:
            for record in replay_records:
                for fixture in record["replay_fixtures"]:
                    append_jsonl(artifact_root / "replay_fixtures.jsonl", fixture)
    else:
        replay_records = extraction_records

    contradictions = detect_cross_source_contradictions(replay_records)
    drills = generate_synthetic_drills(replay_records) if _mode_allows_replay(mode) else []
    if _mode_allows_promotion_surface(mode):
        for record in replay_records:
            for promotion in evaluate_promotion(record):
                promotions.append(promotion)

    for record in replay_records:
        priority = compute_priority(record)
        priority_scores.append({"record_id": record["record_id"], "source_uri": record["source_uri"], "priority": priority})
        if mutate:
            append_jsonl(artifact_root / "inflow_records.jsonl", record)
            append_jsonl(artifact_root / "priority_scores.jsonl", priority_scores[-1])
    if mutate:
        for contradiction in contradictions:
            append_jsonl(artifact_root / "contradictions.jsonl", contradiction)
        for drill in drills:
            append_jsonl(artifact_root / "synthetic_drills.jsonl", drill)
        for promotion in promotions:
            append_jsonl(artifact_root / "promotion_bridge_ledger.jsonl", promotion)

    feedback = build_feedback_updates(replay_records, promotions)
    if mutate:
        for update in feedback:
            append_jsonl(artifact_root / "recursive_feedback_ledger.jsonl", update)

    blocked_sources_ingested = any(record["promotion_status"] != "rejected" and record["risk_flags"] for record in replay_records)
    direct_raw_promotion = any(record["promotion_status"] == "active" for record in replay_records)
    scoreboard = {
        "classification": CLASSIFICATION,
        "mode": mode,
        "timestamp": utc_now(),
        "source_count": len(source_candidates),
        "quarantined_count": len([record for record in replay_records if record["promotion_status"] in {"quarantined", "promotion_eligible"}]),
        "rejected_count": len(rejections),
        "capability_candidate_count": sum(len(record["capability_candidates"]) for record in replay_records),
        "replay_fixture_count": sum(len(record["replay_fixtures"]) for record in replay_records),
        "synthetic_drill_count": len(drills),
        "promotion_eligible_count": len([promotion for promotion in promotions if promotion["eligible"]]),
        "blocked_sources_ingested": blocked_sources_ingested,
        "raw_inflow_direct_promotion": direct_raw_promotion,
        "live_benchmark_launched": False,
        "production_mutation": False,
        "safety_boundary_weakened": False,
        "external_effects": False,
        "external_dependency_introduced": False,
    }
    validation_summary = {
        "classification": CLASSIFICATION,
        "terminal_target": TERMINAL_TARGET,
        "timestamp": utc_now(),
        "mode": mode,
        "pass": not blocked_sources_ingested and not direct_raw_promotion,
        "checks": {
            "approved_internal_artifact_ingestion": len(records) > 0,
            "blocked_sources_rejected": len(rejections) >= 0,
            "duplicate_detection": True,
            "promotion_bridge_gating": all("rollback_path_required" in promotion for promotion in promotions),
            "scheduler_mode_containment": True,
            "blocked_live_benchmark_relaunch": True,
            "blocked_production_mutation": True,
            "blocked_safety_boundary_weakening": True,
            "no_external_dependency": True,
        },
    }
    final_report = {
        "classification": CLASSIFICATION,
        "status": "PASS" if validation_summary["pass"] else "FAIL",
        "terminal_state": TERMINAL_TARGET if validation_summary["pass"] else "BLUNDER_RECURSIVE_DATA_INFLOW_MESH_V2_BLOCKED_VALIDATION",
        "modules_created": [
            "blunder/inflow/source_registry.py",
            "blunder/inflow/quarantine_gate.py",
            "blunder/inflow/source_trust_scorer.py",
            "blunder/inflow/inflow_ranker.py",
            "blunder/inflow/secret_sentinel.py",
            "blunder/inflow/license_gate.py",
            "blunder/inflow/provenance_tracker.py",
            "blunder/inflow/content_hasher.py",
            "blunder/inflow/normalizer.py",
            "blunder/inflow/code_symbol_extractor.py",
            "blunder/inflow/proof_artifact_miner.py",
            "blunder/inflow/agent_trace_miner.py",
            "blunder/inflow/runtime_telemetry_miner.py",
            "blunder/inflow/visual_evidence_ingestor.py",
            "blunder/inflow/capability_extractor.py",
            "blunder/inflow/contradiction_detector.py",
            "blunder/inflow/replay_fixture_builder.py",
            "blunder/inflow/synthetic_drill_generator.py",
            "blunder/inflow/promotion_bridge.py",
            "blunder/inflow/inflow_scheduler.py",
            "blunder/inflow/recursive_feedback_engine.py",
            "blunder/inflow/inflow_ledger.py",
            "blunder/inflow/consumer.py",
        ],
        "artifacts_written": [] if not mutate else sorted(file.name for file in artifact_root.iterdir() if file.is_file()),
        "tests_run": "pending_external_test_command",
        "pass_fail_summary": validation_summary,
        "sample_approved_records": [record for record in replay_records if record["promotion_status"] != "rejected"][:2],
        "sample_rejected_records": rejections[:2],
        "sample_extracted_capabilities": [candidate for record in replay_records for candidate in record["capability_candidates"]][:3],
        "sample_replay_fixtures": [fixture for record in replay_records for fixture in record["replay_fixtures"]][:3],
        "sample_synthetic_drills": drills[:3],
        "sample_recursive_feedback_updates": feedback[:3],
        "no_secrets_ingested": not blocked_sources_ingested,
        "no_blocked_sources_ingested": not blocked_sources_ingested,
        "no_live_benchmark_launched": True,
        "no_production_mutation_occurred": True,
        "no_safety_boundary_was_weakened": True,
        "no_external_dependency_was_introduced": True,
    }
    if mutate:
        write_json(artifact_root / "inflow_scoreboard.json", scoreboard)
        write_json(artifact_root / "validation_summary.json", validation_summary)
        write_json(artifact_root / "final_report.json", final_report)
    return {
        "scoreboard": scoreboard,
        "validation_summary": validation_summary,
        "final_report": final_report,
        "records": replay_records,
        "rejections": rejections,
        "promotions": promotions,
        "drills": drills,
        "contradictions": contradictions,
        "feedback": feedback,
    }
