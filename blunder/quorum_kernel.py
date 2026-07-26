from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

from blunder.deterministic_shards import SHARD_SCHEMA
from blunder.native_agent import validate_change_path
from blunder.physiology import default_opportunity_queue, homeostatic_plan, select_opportunities
from blunder.quorum_graph import GRAPH_SCHEMA, build_quorum_graph, canonical_sha256
from blunder.promotion_policy import (
    EVIDENCE_SCHEMA as PROMOTION_EVIDENCE_SCHEMA,
    PromotionEvidenceV1,
    PromotionScopeV1,
    evaluate_autonomous_promotion,
)


KERNEL_SCHEMA = "obtuse.blunder.quorum-graph-kernel.v1"
SHA256_PATTERN_LENGTH = 64
QUORUM_ROLES: frozenset[str] = frozenset({"proposer", "skeptic", "judge"})
APPROVAL_VERDICT = "APPROVE"


@dataclass(frozen=True)
class QuorumVote:
    role: str
    context_id: str
    candidate_id: str
    proposal_sha256: str
    verdict: str
    evidence_sha256: tuple[str, ...]
    critical_findings: tuple[str, ...]


@dataclass(frozen=True)
class MetabolicBudget:
    maximum_tasks: int
    maximum_seconds: int
    maximum_cpu_percent: float
    minimum_available_memory_bytes: int


def string_sequence(value: object, label: str, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list.")
    result = tuple(str(item).strip() for item in value)
    if any(not item for item in result) or (not allow_empty and not result):
        raise ValueError(f"{label} contains an empty value or is unexpectedly empty.")
    return result


def require_sha256(value: object, label: str) -> str:
    result = str(value or "").strip().upper()
    if len(result) != SHA256_PATTERN_LENGTH or any(character not in "0123456789ABCDEF" for character in result):
        raise ValueError(f"{label} must be a 64-character hexadecimal SHA-256 digest.")
    return result


def parse_vote(value: Mapping[str, object]) -> QuorumVote:
    role = str(value.get("role") or "").strip().lower()
    if role not in QUORUM_ROLES:
        raise ValueError(f"Quorum vote role must be proposer, skeptic, or judge: {role}")
    context_id = str(value.get("contextId") or "").strip()
    candidate_id = str(value.get("candidateId") or "").strip()
    if len(context_id) < 8 or len(candidate_id) < 3:
        raise ValueError(f"Quorum {role} requires a stable contextId and candidateId.")
    verdict = str(value.get("verdict") or "").strip().upper()
    if verdict not in {"APPROVE", "REJECT", "ABSTAIN"}:
        raise ValueError(f"Quorum {role} verdict must be APPROVE, REJECT, or ABSTAIN.")
    evidence = string_sequence(value.get("evidenceSha256"), f"Quorum {role} evidenceSha256", False)
    validated_evidence = tuple(require_sha256(item, f"Quorum {role} evidence digest") for item in evidence)
    return QuorumVote(
        role=role,
        context_id=context_id,
        candidate_id=candidate_id,
        proposal_sha256=require_sha256(value.get("proposalSha256"), f"Quorum {role} proposalSha256"),
        verdict=verdict,
        evidence_sha256=validated_evidence,
        critical_findings=string_sequence(value.get("criticalFindings"), f"Quorum {role} criticalFindings", True),
    )


def validate_candidate_paths(root: Path, value: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    paths = string_sequence(value, "Quorum candidate changedPaths", False)
    normalized = tuple(dict.fromkeys(path.replace("\\", "/").strip("/") for path in paths))
    errors: list[str] = []
    for path in normalized:
        try:
            validate_change_path(root, path)
        except ValueError as error:
            errors.append(str(error))
    return normalized, tuple(errors)


def validate_change_set(value: object) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, dict) for item in value):
        raise ValueError("Quorum candidate changeSet must be a non-empty object list.")
    if len(value) > 32:
        raise ValueError("Quorum candidate changeSet exceeds 32 files.")
    changes: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").replace("\\", "/").strip("/")
        if not path:
            raise ValueError("Quorum candidate changeSet contains an empty path.")
        changes.append({"path": path, "contentSha256": require_sha256(item.get("contentSha256"), f"Change-set digest for {path}")})
    if len({item["path"].lower() for item in changes}) != len(changes):
        raise ValueError("Quorum candidate changeSet contains duplicate paths.")
    return tuple(sorted(changes, key=lambda item: item["path"]))


def shard_reports(value: Sequence[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
    reports: list[dict[str, object]] = []
    for report in value:
        if report.get("schema") != SHARD_SCHEMA:
            raise ValueError("Quorum shard evidence uses an unsupported schema.")
        reports.append(dict(report))
    return tuple(reports)


def gate_record(name: str, passed: bool, detail: str) -> dict[str, object]:
    return {"name": name, "passed": passed, "detail": detail}


def quorum_decision(
    root: Path,
    graph: Mapping[str, object],
    candidate: Mapping[str, object],
    votes: Sequence[QuorumVote],
    raw_shard_reports: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    if graph.get("schema") != GRAPH_SCHEMA:
        raise ValueError("Quorum decision requires a supported semantic graph.")
    if len(votes) != 3:
        raise ValueError("A quorum decision requires exactly three role votes.")
    candidate_id = str(candidate.get("candidateId") or "").strip()
    if len(candidate_id) < 3:
        raise ValueError("Quorum candidate requires a stable candidateId.")
    proposal_sha256 = require_sha256(candidate.get("proposalSha256"), "Quorum candidate proposalSha256")
    checkpoint_sha256 = require_sha256(candidate.get("checkpointSha256"), "Quorum candidate checkpointSha256")
    base_graph_fingerprint = require_sha256(candidate.get("baseGraphFingerprint"), "Quorum candidate baseGraphFingerprint")
    changed_paths, path_errors = validate_candidate_paths(root.resolve(), candidate.get("changedPaths"))
    change_set = validate_change_set(candidate.get("changeSet"))
    reports = shard_reports(raw_shard_reports)
    roles = {vote.role for vote in votes}
    contexts = {vote.context_id for vote in votes}
    critical_findings = tuple(f"{vote.role}: {finding}" for vote in votes for finding in vote.critical_findings)
    gates = [
        gate_record("complete-role-quorum", roles == QUORUM_ROLES, ", ".join(sorted(roles))),
        gate_record("independent-contexts", len(contexts) == 3, f"distinct={len(contexts)}"),
        gate_record(
            "candidate-binding",
            all(vote.candidate_id == candidate_id for vote in votes),
            candidate_id,
        ),
        gate_record(
            "proposal-binding",
            all(vote.proposal_sha256 == proposal_sha256 for vote in votes),
            proposal_sha256,
        ),
        gate_record(
            "change-set-content-binding",
            proposal_sha256 == canonical_sha256(list(change_set))
            and {item["path"] for item in change_set} == set(changed_paths),
            f"files={len(change_set)}",
        ),
        gate_record(
            "base-graph-binding",
            base_graph_fingerprint == graph.get("fingerprint"),
            base_graph_fingerprint,
        ),
        gate_record("changed-path-confinement", not path_errors, "; ".join(path_errors) if path_errors else "bounded"),
        gate_record(
            "unanimous-approval",
            all(vote.verdict == APPROVAL_VERDICT for vote in votes),
            ", ".join(f"{vote.role}={vote.verdict}" for vote in sorted(votes, key=lambda item: item.role)),
        ),
        gate_record("no-critical-findings", not critical_findings, "; ".join(critical_findings) if critical_findings else "clear"),
        gate_record(
            "deterministic-shard-proof",
            bool(reports)
            and all(report.get("status") == "PASS" for report in reports)
            and all(report.get("graphFingerprint") == base_graph_fingerprint for report in reports),
            f"reports={len(reports)}",
        ),
        gate_record("verification-court", candidate.get("courtVerdict") == "APPROVE", str(candidate.get("courtVerdict") or "missing")),
        gate_record("regression-free", candidate.get("regressionFree") is True, str(candidate.get("regressionFree"))),
        gate_record("rollback-checkpoint-bound", bool(checkpoint_sha256), checkpoint_sha256),
    ]
    approved = all(bool(gate["passed"]) for gate in gates)
    failed_gates = tuple(str(gate["name"]) for gate in gates if gate["passed"] is not True)
    applied = candidate.get("appliedToReviewTree") is True
    proof_payload = {
        "candidateId": candidate_id,
        "proposalSha256": proposal_sha256,
        "checkpointSha256": checkpoint_sha256,
        "baseGraphFingerprint": base_graph_fingerprint,
        "changedPaths": list(changed_paths),
        "changeSet": list(change_set),
        "votes": [asdict(vote) for vote in sorted(votes, key=lambda item: item.role)],
        "shards": [report.get("transcriptSha256") for report in reports],
        "gates": gates,
    }
    proof_sha256 = canonical_sha256(proof_payload)
    promotion = evaluate_autonomous_promotion(
        PromotionScopeV1.IMPROVEMENT,
        PromotionEvidenceV1(
            PROMOTION_EVIDENCE_SCHEMA,
            candidate_id,
            proof_sha256,
            tuple(
                dict.fromkeys(
                    (
                        proposal_sha256,
                        checkpoint_sha256,
                        base_graph_fingerprint,
                        *(str(report.get("transcriptSha256")) for report in reports),
                    )
                )
            ),
            {
                "constitutional": approved,
                "safety": candidate.get("regressionFree") is True,
                "provenance": approved,
                "rollback": bool(checkpoint_sha256),
                "authority": not applied,
                "budget": candidate.get("equalBudget") is True,
                "reproducibility": bool(reports) and all(report.get("repeatable") is True for report in reports),
            },
            1.0 if approved else 0.0,
            float(candidate.get("confidence") or 0.0),
            float(candidate.get("uncertainty") or 1.0),
            len({vote.context_id for vote in votes}),
            int(candidate.get("independentModels") or 0),
            int(candidate.get("domains") or 0),
            int(candidate.get("holdoutTrials") or 0),
            float(candidate.get("holdoutSuccessRate") or 0.0),
            float(candidate.get("improvementRatio") or 0.0),
            float(candidate.get("costRatio") or 1_000.0),
            candidate.get("contaminated") is True,
            tuple(str(item) for item in candidate.get("contradictions", []) if str(item).strip())
            if isinstance(candidate.get("contradictions", []), list)
            else ("invalid-contradictions",),
            bool(checkpoint_sha256),
            approved,
            not applied,
            candidate.get("equalBudget") is True,
            not applied,
            bool(reports) and all(report.get("repeatable") is True for report in reports),
        ),
    )
    status = promotion.status if approved else "ROLLBACK_REQUIRED" if applied else "QUARANTINED"
    return {
        "schema": "obtuse.blunder.quorum-decision.v1",
        "status": status,
        "candidateId": candidate_id,
        "proofSha256": proof_sha256,
        "proofClass": "deterministic-evidence-attestation",
        "formalMathematicalProof": False,
        "gates": gates,
        "failedGates": list(failed_gates),
        "votes": proof_payload["votes"],
        "shardTranscriptSha256": proof_payload["shards"],
        "promotionDecisionDigest": promotion.decision_digest,
        "promotionFailedGates": list(promotion.failed_gates),
        "automaticPromotionAuthorized": promotion.automatic_promotion_authorized,
        "immuneResponse": {
            "quarantined": not approved,
            "reasons": list(failed_gates),
            "rollbackRequired": applied and not approved,
            "compromisedSubsystemIsolation": "REVIEW_CANDIDATE_ONLY" if not approved else "NOT_REQUIRED",
        },
        "authority": {
            "reviewTreeMutation": False,
            "integration": False,
            "commit": False,
            "push": False,
            "deploy": False,
            "publicRelease": False,
            "operatorConfirmationRequired": False,
        },
    }


def timeline_report(
    graph: Mapping[str, object],
    candidate: Mapping[str, object],
    decision: Mapping[str, object],
    raw_shard_reports: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    if graph.get("schema") != GRAPH_SCHEMA:
        raise ValueError("Timeline requires a supported semantic graph.")
    candidate_id = str(candidate.get("candidateId") or "candidate")
    nodes: list[dict[str, object]] = [
        {"id": "base", "kind": "semantic-graph", "digest": graph.get("fingerprint"), "state": "CANONICAL_SOURCE_VIEW"},
        {"id": candidate_id, "kind": "candidate", "digest": candidate.get("proposalSha256"), "state": "ISOLATED_PROPOSAL"},
    ]
    edges: list[dict[str, str]] = [{"from": "base", "to": candidate_id, "relation": "counterfactual-fork"}]
    for index, report in enumerate(raw_shard_reports, start=1):
        shard_id = f"shard-{index}"
        nodes.append({"id": shard_id, "kind": "micro-space", "digest": report.get("transcriptSha256"), "state": report.get("status")})
        edges.append({"from": candidate_id, "to": shard_id, "relation": "validated-in"})
    nodes.extend(
        [
            {"id": "quorum", "kind": "consensus", "digest": decision.get("proofSha256"), "state": decision.get("status")},
            {"id": "promotion-threshold-gate", "kind": "authority", "digest": decision.get("promotionDecisionDigest"), "state": decision.get("status")},
        ]
    )
    for index in range(1, len(raw_shard_reports) + 1):
        edges.append({"from": f"shard-{index}", "to": "quorum", "relation": "evidence-for"})
    edges.append({"from": "quorum", "to": "promotion-threshold-gate", "relation": "evaluated-by"})
    payload = {"nodes": nodes, "edges": edges}
    return {
        "schema": "obtuse.blunder.counterfactual-timeline.v1",
        "status": "SEALED",
        "timelineSha256": canonical_sha256(payload),
        "nodes": nodes,
        "edges": edges,
        "automaticMerge": False,
        "automaticDeletion": False,
    }


def parse_metabolic_budget(value: Mapping[str, object]) -> MetabolicBudget:
    maximum_tasks = value.get("maximumTasks")
    maximum_seconds = value.get("maximumSeconds")
    maximum_cpu = value.get("maximumCpuPercent")
    minimum_memory = value.get("minimumAvailableMemoryBytes")
    if not isinstance(maximum_tasks, int) or isinstance(maximum_tasks, bool) or maximum_tasks < 1 or maximum_tasks > 8:
        raise ValueError("Metabolic maximumTasks must be an integer from one through eight.")
    if not isinstance(maximum_seconds, int) or isinstance(maximum_seconds, bool) or maximum_seconds < 30 or maximum_seconds > 1800:
        raise ValueError("Metabolic maximumSeconds must be an integer from 30 through 1800.")
    if not isinstance(maximum_cpu, (int, float)) or isinstance(maximum_cpu, bool) or maximum_cpu < 5 or maximum_cpu > 90:
        raise ValueError("Metabolic maximumCpuPercent must be from 5 through 90.")
    if not isinstance(minimum_memory, int) or isinstance(minimum_memory, bool) or minimum_memory < 256 * 1024 * 1024:
        raise ValueError("Metabolic minimumAvailableMemoryBytes must be at least 256 MiB.")
    return MetabolicBudget(maximum_tasks, maximum_seconds, float(maximum_cpu), minimum_memory)


def metabolic_plan(
    graph: Mapping[str, object],
    telemetry: Mapping[str, object],
    budget: MetabolicBudget,
) -> dict[str, object]:
    if graph.get("schema") != GRAPH_SCHEMA:
        raise ValueError("Metabolic planning requires a supported semantic graph.")
    cpu_percent = telemetry.get("cpuPercent")
    memory_bytes = telemetry.get("availableMemoryBytes")
    thermal = str(telemetry.get("thermalState") or "").strip().lower()
    if not isinstance(cpu_percent, (int, float)) or isinstance(cpu_percent, bool) or not 0 <= cpu_percent <= 100:
        raise ValueError("Metabolic telemetry cpuPercent must be from zero through 100.")
    if not isinstance(memory_bytes, int) or isinstance(memory_bytes, bool) or memory_bytes < 0:
        raise ValueError("Metabolic telemetry availableMemoryBytes must be a non-negative integer.")
    if thermal not in {"cool", "nominal", "warm", "hot", "unknown"}:
        raise ValueError("Metabolic thermalState must be cool, nominal, warm, hot, or unknown.")
    gates = {
        "operatorIdle": telemetry.get("operatorIdle") is True,
        "foregroundMissionAbsent": telemetry.get("foregroundMissionActive") is False,
        "cpuBudgetAvailable": float(cpu_percent) <= budget.maximum_cpu_percent,
        "memoryBudgetAvailable": memory_bytes >= budget.minimum_available_memory_bytes,
        "thermalBudgetAvailable": thermal in {"cool", "nominal"},
        "queueFailuresAbsent": int(telemetry.get("queueFailures") or 0) == 0,
        "destructiveAuthorityAbsent": telemetry.get("destructiveAuthority") is not True,
    }
    ready = all(gates.values())
    failed = [name for name, passed in gates.items() if not passed]
    if thermal == "unknown":
        health_status = "UNKNOWN"
    elif not gates["cpuBudgetAvailable"] or not gates["memoryBudgetAvailable"] or not gates["thermalBudgetAvailable"]:
        health_status = "OVERLOADED"
    elif not gates["queueFailuresAbsent"]:
        health_status = "RECOVERY_BOUND"
    elif ready:
        health_status = "HEALTHY"
    else:
        health_status = "DEGRADED"
    opportunities = default_opportunity_queue(graph)
    selection = select_opportunities(
        opportunities,
        budget.maximum_tasks,
        budget.maximum_seconds,
        health_status,
        (),
    )
    selected = selection["selected"] if ready else []
    elapsed = selection["plannedSeconds"] if ready else 0
    return {
        "schema": "obtuse.blunder.metabolic-plan.v1",
        "status": "READY_FOR_BOUNDED_IDLE_ANALYSIS" if ready else "PAUSED_RESOURCE_OR_SAFETY_GATE",
        "graphFingerprint": graph.get("fingerprint"),
        "telemetry": dict(telemetry),
        "budget": asdict(budget),
        "gates": gates,
        "failedGates": failed,
        "healthStatus": health_status,
        "opportunityQueue": selection,
        "homeostaticPlan": homeostatic_plan(health_status, 4, budget.maximum_tasks),
        "selectedTasks": selected,
        "plannedSeconds": elapsed,
        "authority": {
            "advisoryOnly": True,
            "providerCalls": 0,
            "sourceMutation": False,
            "queueMutation": False,
            "automaticTranspilation": False,
            "resourceSaturation": False,
        },
    }


def kernel_snapshot(root: Path) -> dict[str, object]:
    graph = build_quorum_graph(root.resolve(), True)
    counts = graph.get("counts") if isinstance(graph.get("counts"), dict) else {}
    return {
        "schema": KERNEL_SCHEMA,
        "status": "OPERATIONAL",
        "name": "Blunder Quorum Graph Kernel",
        "graph": {
            "schema": graph.get("schema"),
            "fingerprint": graph.get("fingerprint"),
            "repositoryFingerprint": graph.get("repositoryFingerprint"),
            "counts": counts,
            "changedPaths": graph.get("changedPaths"),
            "sourceFilesRemainCanonical": True,
        },
        "surfaces": {
            "P31": "CONTENT_ADDRESSED_SEMANTIC_GRAPH",
            "P32": "REACTIVE_IMPACT_FRONTIER",
            "P33": "JOB_OBJECT_VALIDATION_MICROSPACES",
            "P34": "DETERMINISTIC_VARIANT_TOURNAMENT",
            "P35": "THREE_CONTEXT_UNANIMOUS_QUORUM",
            "P36": "IMMUNE_QUARANTINE_AND_ROLLBACK_SIGNAL",
            "P37": "RESOURCE_GATED_METABOLIC_PLANNER",
            "P38": "SEALED_COUNTERFACTUAL_TIMELINE",
            "P39": "PROOF_CARRYING_AUTONOMOUS_THRESHOLD_PROMOTION",
            "P40": "KERNEL_CONTROL_PLANE",
        },
        "quorum": {
            "roles": sorted(QUORUM_ROLES),
            "requiredAgreement": "UNANIMOUS",
            "independentContextsRequired": 3,
            "status": "AWAITING_CANDIDATE_EVIDENCE",
        },
        "microspaces": {
            "commandKinds": ["python-unittest", "python-compile", "node-check"],
            "arbitraryCommands": False,
            "kernelProcessLimitRequested": 1,
            "determinismMeasuredByRepeatedNormalizedOutput": True,
            "wasmClaimed": False,
        },
        "metabolism": {
            "status": "TELEMETRY_REQUIRED",
            "operatorIdleRequired": True,
            "foregroundMissionAbsentRequired": True,
            "thermalAndMemoryGatesRequired": True,
            "resourceSaturationAllowed": False,
        },
        "authority": {
            "sourceMutation": False,
            "automaticMerge": False,
            "automaticCommit": False,
            "automaticPush": False,
            "deployment": False,
            "publicRelease": False,
        },
    }
