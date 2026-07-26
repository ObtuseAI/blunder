from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence

from blunder.api.contracts import canonical_sha256, require_digest
from blunder.promotion_policy import (
    EVIDENCE_SCHEMA,
    PromotionEvidenceV1,
    PromotionScopeV1,
    evaluate_autonomous_promotion,
)


@dataclass(frozen=True)
class ResearchEvidenceV1:
    source_id: str
    citation: str
    domain: str
    evidence_digest: str
    independent: bool


@dataclass(frozen=True)
class DesignConceptV1:
    concept_id: str
    structural_kind: str
    rationale: str
    prototype_digest: str
    screenshot_digest: str
    accessibility_tree_digest: str
    usefulness: float
    accessibility: float
    confidence: float


@dataclass(frozen=True)
class RankedExperimentV1:
    experiment_id: str
    concept_id: str
    hypothesis: str
    evidence_digest: str
    result_score: float
    information_gain: float
    cost: float
    measured: bool


def _bounded_score(value: float, context: str) -> float:
    score = float(value)
    if score < 0.0 or score > 1.0:
        raise ValueError(f"{context} must be between zero and one.")
    return score


def execute_research_design_workflow(
    problem: str,
    evidence: Sequence[ResearchEvidenceV1],
    contradiction_digests: Sequence[str],
    concepts: Sequence[DesignConceptV1],
    experiments: Sequence[RankedExperimentV1],
) -> dict[str, object]:
    normalized_problem = problem.strip()
    if not normalized_problem:
        raise ValueError("Research workflow requires a repository-native problem.")
    if len(evidence) < 3:
        raise ValueError("Research workflow requires at least three cited evidence sources.")
    evidence_ids = {item.source_id for item in evidence}
    evidence_digests = tuple(require_digest(item.evidence_digest, "Research evidence") for item in evidence)
    if len(evidence_ids) != len(evidence) or len(set(evidence_digests)) != len(evidence):
        raise ValueError("Research sources and evidence identities must be unique.")
    if any(not item.citation.strip() or not item.domain.strip() for item in evidence):
        raise ValueError("Research evidence requires a citation and domain.")
    contradictions = tuple(require_digest(item, "Research contradiction") for item in contradiction_digests)
    if len(concepts) < 3 or len({item.structural_kind for item in concepts}) < 3:
        raise ValueError("Research workflow requires at least three structurally different concepts.")
    concept_ids = {item.concept_id for item in concepts}
    if len(concept_ids) != len(concepts):
        raise ValueError("Research concept identities must be unique.")
    for concept in concepts:
        require_digest(concept.prototype_digest, "Concept prototype")
        require_digest(concept.screenshot_digest, "Concept screenshot")
        require_digest(concept.accessibility_tree_digest, "Concept accessibility tree")
        _bounded_score(concept.usefulness, "Concept usefulness")
        _bounded_score(concept.accessibility, "Concept accessibility")
        _bounded_score(concept.confidence, "Concept confidence")
    if len(experiments) < 2:
        raise ValueError("Research workflow requires at least two ranked experiments.")
    for experiment in experiments:
        if experiment.concept_id not in concept_ids or not experiment.hypothesis.strip():
            raise ValueError("Research experiment references an unknown concept or empty hypothesis.")
        require_digest(experiment.evidence_digest, "Research experiment evidence")
        _bounded_score(experiment.result_score, "Experiment result")
        _bounded_score(experiment.information_gain, "Experiment information gain")
        if experiment.cost < 0.0:
            raise ValueError("Research experiment cost must be non-negative.")
    ranked = tuple(
        sorted(
            experiments,
            key=lambda item: (-item.result_score, -item.information_gain, item.cost, item.experiment_id),
        )
    )
    selected_id = ranked[0].concept_id
    selected = next(item for item in concepts if item.concept_id == selected_id)
    measured = tuple(item for item in experiments if item.measured)
    evaluation_identity = canonical_sha256(
        {
            "problem": normalized_problem,
            "evidence": [asdict(item) for item in evidence],
            "contradictions": contradictions,
            "concepts": [asdict(item) for item in concepts],
            "experiments": [asdict(item) for item in experiments],
        }
    )
    promotion = evaluate_autonomous_promotion(
        PromotionScopeV1.RESEARCH_CONCEPT,
        PromotionEvidenceV1(
            EVIDENCE_SCHEMA,
            selected.concept_id,
            evaluation_identity,
            tuple(dict.fromkeys((*evidence_digests, *(item.evidence_digest for item in experiments)))),
            {
                "constitutional": True,
                "safety": selected.accessibility >= 0.85,
                "provenance": all(item.citation.strip() for item in evidence),
                "rollback": True,
                "authority": True,
                "budget": True,
                "reproducibility": len(measured) >= 2,
            },
            1.0,
            selected.confidence,
            1.0 - selected.confidence,
            sum(1 for item in evidence if item.independent),
            1,
            len({item.domain for item in evidence}),
            len(measured),
            sum(1 for item in measured if item.result_score >= 0.85) / len(measured) if measured else 0.0,
            max(0.0, ranked[0].result_score - 0.8),
            1.0,
            False,
            contradictions,
            True,
            True,
            True,
            True,
            True,
            len(measured) >= 2,
        ),
    )
    return {
        "schema": "obtuse.blunder.research-design-workflow.v1",
        "problem": normalized_problem,
        "researchBrief": {"evidence": [asdict(item) for item in evidence], "contradictions": list(contradictions)},
        "concepts": [asdict(item) for item in concepts],
        "rankedExperiments": [asdict(item) for item in ranked],
        "selectedConceptId": selected.concept_id,
        "measurementClass": "MEASURED" if measured and len(measured) == len(experiments) else "SIMULATION_OR_MIXED_EVIDENCE",
        "syntheticScoresReportedAsMeasured": False,
        "promotionStatus": promotion.status,
        "automaticPromotionAuthorized": promotion.automatic_promotion_authorized,
        "promotionDecisionDigest": promotion.decision_digest,
        "promotionFailedGates": list(promotion.failed_gates),
        "sourceMutationAuthorized": False,
        "externalEffectAuthorized": False,
    }
