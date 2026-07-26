"""Fail-closed vNext projections over existing CCOS control substrates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from blunder.api.contracts import canonical_sha256
from blunder.physiology import cognitive_physics
from blunder.promotion_policy import (
    EVIDENCE_SCHEMA,
    PromotionEvidenceV1,
    PromotionScopeV1,
    evaluate_autonomous_promotion,
)


MEMORY_LAYER_TO_CCOS_TIER: Mapping[str, str] = {
    "working": "episode",
    "episodic": "mission",
    "semantic": "pattern",
    "strategy": "principle",
    "design": "architecture",
    "failure": "capability",
    "calibration": "theory",
}


@dataclass(frozen=True)
class ControlDecisionV1:
    schema: str
    action: str
    reasons: tuple[str, ...]
    evidence_completeness: float
    uncertainty: float
    information_gain: float
    saturated: bool


def metacognitive_decision(
    evidence_completeness: float,
    uncertainty: float,
    expected_information_gain: float,
    budget_remaining_ratio: float,
    hard_gates_passed: bool,
) -> ControlDecisionV1:
    values = (evidence_completeness, uncertainty, expected_information_gain, budget_remaining_ratio)
    if any(value < 0.0 or value > 1.0 for value in values):
        raise ValueError("Metacognitive measurements must be between zero and one.")
    reasons: list[str] = []
    saturated = expected_information_gain <= 0.1
    if not hard_gates_passed:
        action = "STOP_HARD_GATE"
        reasons.append("A safety, provenance, rollback, or authority gate failed.")
    elif budget_remaining_ratio <= 0.05:
        action = "STOP_BUDGET_EXHAUSTED"
        reasons.append("The remaining bounded resource budget is insufficient.")
    elif evidence_completeness >= 0.9 and uncertainty <= 0.15 and saturated:
        action = "READY_FOR_REVIEW"
        reasons.append("Evidence is complete, uncertainty is bounded, and expected information gain is saturated.")
    elif saturated and uncertainty > 0.35:
        action = "REDIRECT_STRATEGY"
        reasons.append("The current strategy is saturated while material uncertainty remains.")
    else:
        action = "CONTINUE_BOUNDED"
        reasons.append("Expected information gain justifies another bounded step.")
    return ControlDecisionV1(
        "obtuse.blunder.metacognitive-decision.v1",
        action,
        tuple(reasons),
        evidence_completeness,
        uncertainty,
        expected_information_gain,
        saturated,
    )


def cognitive_measurement(
    probabilities: Sequence[float],
    previous_utility: float,
    current_utility: float,
    elapsed_seconds: float,
    unresolved_items: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    return cognitive_physics(probabilities, previous_utility, current_utility, elapsed_seconds, unresolved_items)


def memory_projection(
    project_id: str,
    layer: str,
    claim: str,
    evidence_digests: Sequence[str],
    contradictions: Sequence[str],
) -> dict[str, object]:
    if layer not in MEMORY_LAYER_TO_CCOS_TIER:
        raise ValueError(f"Unsupported vNext memory layer: {layer}")
    evidence = tuple(sorted(set(evidence_digests)))
    if not project_id.strip() or not claim.strip() or not evidence:
        raise ValueError("Memory projections require project identity, a claim, and evidence.")
    if any(len(item) != 64 for item in evidence):
        raise ValueError("Memory projection evidence identities must be SHA-256 digests.")
    payload = {
        "schema": "obtuse.blunder.memory-projection.v1",
        "projectId": project_id,
        "layer": layer,
        "ccosTier": MEMORY_LAYER_TO_CCOS_TIER[layer],
        "claim": claim,
        "evidenceDigests": list(evidence),
        "contradictions": list(contradictions),
        "authority": {
            "sourceMutation": False,
            "permissionCreation": False,
            "candidateActivation": False,
            "providerCalls": 0,
            "networkCalls": 0,
        },
    }
    return payload | {"projectionDigest": canonical_sha256(payload)}


def consolidate_memory(project_id: str, records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    normalized_project = project_id.strip()
    if not normalized_project:
        raise ValueError("Memory consolidation requires a project identity.")
    retained_by_claim: dict[tuple[str, str], dict[str, object]] = {}
    superseded: list[dict[str, str]] = []
    contradiction_pairs: set[tuple[str, str]] = set()
    for raw in records:
        if raw.get("schema") != "obtuse.blunder.memory-projection.v1":
            raise ValueError("Memory consolidation accepts only MemoryProjectionV1 records.")
        if raw.get("projectId") != normalized_project:
            raise PermissionError("Memory consolidation cannot cross project boundaries.")
        layer = str(raw.get("layer") or "")
        claim = str(raw.get("claim") or "").strip()
        projection_digest = str(raw.get("projectionDigest") or "").upper()
        if layer not in MEMORY_LAYER_TO_CCOS_TIER or not claim or len(projection_digest) != 64:
            raise ValueError("Memory consolidation record identity, layer, or claim is invalid.")
        evidence = raw.get("evidenceDigests")
        contradictions = raw.get("contradictions")
        if not isinstance(evidence, list) or not isinstance(contradictions, list):
            raise ValueError("Memory consolidation evidence and contradictions must be lists.")
        key = (layer, claim.casefold())
        current = retained_by_claim.get(key)
        candidate = dict(raw)
        if current is None or len(evidence) > len(current.get("evidenceDigests", [])):
            if current is not None:
                superseded.append(
                    {"prior": str(current["projectionDigest"]), "current": projection_digest, "reason": "stronger-evidence-set"}
                )
            retained_by_claim[key] = candidate
        else:
            superseded.append(
                {"prior": projection_digest, "current": str(current["projectionDigest"]), "reason": "duplicate-or-weaker-evidence-set"}
            )
        for contradiction in contradictions:
            contradiction_text = str(contradiction).strip()
            if contradiction_text:
                contradiction_pairs.add(tuple(sorted((projection_digest, contradiction_text))))
    retained = tuple(sorted(retained_by_claim.values(), key=lambda item: str(item["projectionDigest"])))
    payload = {
        "schema": "obtuse.blunder.memory-consolidation.v1",
        "projectId": normalized_project,
        "status": "CANDIDATES_PROPOSED_OFFLINE",
        "retained": list(retained),
        "superseded": sorted(superseded, key=lambda item: (item["prior"], item["current"])),
        "contradictionPairs": [list(item) for item in sorted(contradiction_pairs)],
        "providerCalls": 0,
        "networkCalls": 0,
        "sourceMutationAuthorized": False,
        "permissionCreationAuthorized": False,
        "automaticCandidateActivationAuthorized": False,
    }
    return payload | {"consolidationDigest": canonical_sha256(payload)}


def forward_evaluation_gate(
    baseline_scores: Sequence[float],
    candidate_scores: Sequence[float],
    equal_budget: bool,
    uncontaminated: bool,
    hard_gates_passed: bool,
    improved_improver_passed: bool,
) -> dict[str, object]:
    if not baseline_scores or len(baseline_scores) != len(candidate_scores):
        raise ValueError("Forward evaluation requires paired non-empty score vectors.")
    if any(value < 0.0 or value > 1.0 for value in (*baseline_scores, *candidate_scores)):
        raise ValueError("Forward evaluation scores must be between zero and one.")
    baseline_mean = sum(baseline_scores) / len(baseline_scores)
    candidate_mean = sum(candidate_scores) / len(candidate_scores)
    improvement_ratio = max(0.0, (candidate_mean - baseline_mean) / baseline_mean) if baseline_mean > 0.0 else 0.0
    evaluation_payload = {
        "baselineScores": list(baseline_scores),
        "candidateScores": list(candidate_scores),
        "equalBudget": equal_budget,
        "uncontaminated": uncontaminated,
        "hardGatesPassed": hard_gates_passed,
        "improvedImproverPassed": improved_improver_passed,
    }
    evaluation_identity = canonical_sha256(evaluation_payload)
    evidence = PromotionEvidenceV1(
        EVIDENCE_SCHEMA,
        canonical_sha256({"candidateScores": list(candidate_scores)}),
        evaluation_identity,
        (evaluation_identity,),
        {
            "constitutional": hard_gates_passed,
            "safety": hard_gates_passed,
            "provenance": uncontaminated,
            "rollback": hard_gates_passed,
            "authority": hard_gates_passed,
            "budget": equal_budget,
            "reproducibility": improved_improver_passed,
        },
        1.0,
        min(candidate_scores),
        1.0 - min(candidate_scores),
        len(candidate_scores),
        2,
        2,
        len(candidate_scores),
        sum(1 for baseline, candidate in zip(baseline_scores, candidate_scores) if candidate > baseline) / len(candidate_scores),
        improvement_ratio,
        1.0 if equal_budget else 1_000.0,
        not uncontaminated,
        (),
        hard_gates_passed,
        uncontaminated,
        hard_gates_passed,
        equal_budget,
        True,
        improved_improver_passed,
    )
    promotion = evaluate_autonomous_promotion(PromotionScopeV1.IMPROVEMENT, evidence)
    return {
        "schema": "obtuse.blunder.forward-evaluation.v1",
        "status": promotion.status,
        "equalBudget": equal_budget,
        "uncontaminated": uncontaminated,
        "hardGatesPassed": hard_gates_passed,
        "improvedImproverPassed": improved_improver_passed,
        "forwardPerformanceImproved": candidate_mean > baseline_mean,
        "candidateSourceMutationAuthorized": False,
        "automaticPromotionAuthorized": promotion.automatic_promotion_authorized,
        "operatorPromotionRequired": False,
        "promotionDecisionDigest": promotion.decision_digest,
        "promotionFailedGates": list(promotion.failed_gates),
    }


def compare_resource_policies(
    static_scores: Sequence[float],
    adaptive_scores: Sequence[float],
    equal_cost: bool,
    uncontaminated: bool,
    hard_gates_passed: bool,
    independent_evaluators: int,
    domains: int,
    rollback_proven: bool,
) -> dict[str, object]:
    if len(static_scores) < 3 or len(static_scores) != len(adaptive_scores):
        raise ValueError("Resource-policy comparison requires at least three paired outcomes.")
    if any(value < 0.0 or value > 1.0 for value in (*static_scores, *adaptive_scores)):
        raise ValueError("Resource-policy scores must be between zero and one.")
    static_mean = sum(static_scores) / len(static_scores)
    adaptive_mean = sum(adaptive_scores) / len(adaptive_scores)
    improvement = max(0.0, (adaptive_mean - static_mean) / static_mean) if static_mean > 0.0 else 0.0
    evaluation_identity = canonical_sha256(
        {
            "staticScores": list(static_scores),
            "adaptiveScores": list(adaptive_scores),
            "equalCost": equal_cost,
            "uncontaminated": uncontaminated,
        }
    )
    decision = evaluate_autonomous_promotion(
        PromotionScopeV1.STRATEGY,
        PromotionEvidenceV1(
            EVIDENCE_SCHEMA,
            canonical_sha256({"strategy": "metacognitive-resource-control"}),
            evaluation_identity,
            tuple(
                canonical_sha256({"index": index, "static": static, "adaptive": adaptive})
                for index, (static, adaptive) in enumerate(zip(static_scores, adaptive_scores))
            ),
            {
                "constitutional": hard_gates_passed,
                "safety": hard_gates_passed,
                "provenance": uncontaminated,
                "rollback": rollback_proven,
                "authority": hard_gates_passed,
                "budget": equal_cost,
                "reproducibility": independent_evaluators >= 2,
            },
            1.0,
            min(adaptive_scores),
            1.0 - min(adaptive_scores),
            len(adaptive_scores),
            independent_evaluators,
            domains,
            len(adaptive_scores),
            sum(1 for static, adaptive in zip(static_scores, adaptive_scores) if adaptive > static)
            / len(adaptive_scores),
            improvement,
            1.0 if equal_cost else 1_000.0,
            not uncontaminated,
            (),
            rollback_proven,
            uncontaminated,
            hard_gates_passed,
            equal_cost,
            True,
            independent_evaluators >= 2,
        ),
    )
    return {
        "schema": "obtuse.blunder.resource-policy-comparison.v1",
        "staticMean": static_mean,
        "adaptiveMean": adaptive_mean,
        "equalCost": equal_cost,
        "status": decision.status,
        "automaticPromotionAuthorized": decision.automatic_promotion_authorized,
        "promotionDecisionDigest": decision.decision_digest,
        "failedGates": list(decision.failed_gates),
    }
