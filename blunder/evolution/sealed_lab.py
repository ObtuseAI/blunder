"""Sealed recursive-evolution primitives for Blunder.

The laboratory evaluates data-only mutation proposals against supplied evidence.
It cannot edit a worktree, activate a candidate, or mutate constitutional rules.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal, Mapping, Sequence

from blunder.promotion_policy import (
    EVIDENCE_SCHEMA as PROMOTION_EVIDENCE_SCHEMA,
    PromotionEvidenceV1,
    PromotionScopeV1,
    evaluate_autonomous_promotion,
)


MUTATION_SCHEMA = "obtuse.blunder.sealed-mutation-ir.v1"
LINEAGE_SCHEMA = "obtuse.blunder.candidate-lineage.v1"
EVALUATION_SCHEMA = "obtuse.blunder.sealed-evolution-evaluation.v1"
ARCHIVE_SCHEMA = "obtuse.blunder.quality-diversity-archive.v1"

MutationLayer = Literal[
    "prompt",
    "context-policy",
    "routing",
    "topology",
    "scheduling",
    "validation",
    "capability-composition",
]

MUTATION_LAYERS: tuple[str, ...] = (
    "prompt",
    "context-policy",
    "routing",
    "topology",
    "scheduling",
    "validation",
    "capability-composition",
)

REALITY_IDS: tuple[str, ...] = (
    "conservative-baseline",
    "aggressive-bounded",
    "adversarial",
    "historical-replay",
    "degraded-dependency",
    "fresh-heldout",
    "synthetic-future",
)

PROMOTION_REALITIES: frozenset[str] = frozenset(REALITY_IDS[:-1])
IMMUTABLE_TARGET_PREFIXES: tuple[str, ...] = (
    "constitution",
    "authority.root",
    "effect-registry.immutable",
    "promotion.constitutional-threshold-policy",
)
PIPELINE_STAGES: tuple[str, ...] = (
    "proposal",
    "static-court",
    "isolated-worktree",
    "deterministic-validation",
    "historical-replay",
    "heldout-evaluation",
    "adversarial-evaluation",
    "fitness-vector",
    "quorum",
    "autonomous-threshold-gate",
)


@dataclass(frozen=True)
class MutationOperation:
    layer: MutationLayer
    target: str
    operator: str
    value: object


@dataclass(frozen=True)
class MutationIR:
    schema: str
    operations: tuple[MutationOperation, ...]
    digest: str


@dataclass(frozen=True)
class CandidateLineage:
    schema: str
    candidate_digest: str
    parent_digests: tuple[str, ...]
    mutation_digest: str
    operator_id: str
    mutation_layer: MutationLayer
    rationale: str
    expected_effect: str
    rollback_digest: str
    compatibility: tuple[str, ...]
    lineage_digest: str


@dataclass(frozen=True)
class RealityPolicy:
    reality_id: str
    synthetic: bool
    promotion_evidence: bool
    required: bool


@dataclass(frozen=True)
class RealityObservation:
    reality_id: str
    success: float
    safety: float
    reproducibility: float
    rollback: float
    novelty: float
    efficiency: float
    confidence: float
    evidence_digest: str
    contaminated: bool
    deterministic: bool


@dataclass(frozen=True)
class EvaluationControls:
    static_court_passed: bool
    isolated_worktree: bool
    deterministic_validation_passed: bool
    quorum_passed: bool
    rollback_drill_passed: bool
    champion_floor: float
    live_tree_before: str
    live_tree_after: str


@dataclass(frozen=True)
class FitnessVector:
    success: float
    safety: float
    reproducibility: float
    rollback: float
    novelty: float
    efficiency: float
    confidence: float


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def _require_digest(value: str, context: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) != 64 or any(character not in "0123456789ABCDEF" for character in normalized):
        raise ValueError(f"{context} requires a 64-character SHA-256 digest.")
    return normalized


def _require_text(value: str, context: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{context} must not be empty.")
    return normalized


def _bounded_score(value: float, context: str) -> float:
    score = float(value)
    if score < 0.0 or score > 1.0:
        raise ValueError(f"{context} must be between 0.0 and 1.0.")
    return score


def build_mutation_ir(operations: Sequence[MutationOperation]) -> MutationIR:
    if not operations:
        raise ValueError("Mutation IR requires at least one operation.")
    normalized: list[MutationOperation] = []
    for operation in operations:
        if operation.layer not in MUTATION_LAYERS:
            raise ValueError(f"Unsupported mutation layer: {operation.layer}")
        target = _require_text(operation.target, "Mutation target")
        if any(target == prefix or target.startswith(f"{prefix}.") for prefix in IMMUTABLE_TARGET_PREFIXES):
            raise PermissionError(f"Mutation target is constitutionally immutable: {target}")
        normalized.append(
            MutationOperation(
                layer=operation.layer,
                target=target,
                operator=_require_text(operation.operator, "Mutation operator"),
                value=operation.value,
            )
        )
    payload = {"schema": MUTATION_SCHEMA, "operations": [asdict(item) for item in normalized]}
    return MutationIR(schema=MUTATION_SCHEMA, operations=tuple(normalized), digest=_canonical_digest(payload))


def build_candidate_lineage(
    mutation: MutationIR,
    parent_digests: Sequence[str],
    operator_id: str,
    rationale: str,
    expected_effect: str,
    rollback_digest: str,
    compatibility: Sequence[str],
) -> CandidateLineage:
    layers = {operation.layer for operation in mutation.operations}
    if len(layers) != 1:
        raise ValueError("A candidate lineage must contain exactly one mutation layer.")
    parents = tuple(_require_digest(item, "Parent") for item in parent_digests)
    if not parents:
        raise ValueError("Candidate lineage requires at least one parent digest.")
    compatible = tuple(_require_text(item, "Compatibility identity") for item in compatibility)
    if not compatible or len(set(compatible)) != len(compatible):
        raise ValueError("Candidate lineage requires unique compatibility identities.")
    rollback = _require_digest(rollback_digest, "Rollback")
    base = {
        "schema": LINEAGE_SCHEMA,
        "parents": parents,
        "mutationDigest": mutation.digest,
        "operatorId": _require_text(operator_id, "Operator id"),
        "mutationLayer": next(iter(layers)),
        "rationale": _require_text(rationale, "Rationale"),
        "expectedEffect": _require_text(expected_effect, "Expected effect"),
        "rollbackDigest": rollback,
        "compatibility": compatible,
    }
    lineage_digest = _canonical_digest(base)
    candidate_digest = _canonical_digest({"lineageDigest": lineage_digest, "mutationDigest": mutation.digest})
    return CandidateLineage(
        schema=LINEAGE_SCHEMA,
        candidate_digest=candidate_digest,
        parent_digests=parents,
        mutation_digest=mutation.digest,
        operator_id=base["operatorId"],
        mutation_layer=base["mutationLayer"],
        rationale=base["rationale"],
        expected_effect=base["expectedEffect"],
        rollback_digest=rollback,
        compatibility=compatible,
        lineage_digest=lineage_digest,
    )


def standard_reality_policies() -> tuple[RealityPolicy, ...]:
    return tuple(
        RealityPolicy(
            reality_id=reality_id,
            synthetic=reality_id == "synthetic-future",
            promotion_evidence=reality_id != "synthetic-future",
            required=True,
        )
        for reality_id in REALITY_IDS
    )


def _validated_observations(observations: Sequence[RealityObservation]) -> dict[str, RealityObservation]:
    indexed: dict[str, RealityObservation] = {}
    for observation in observations:
        if observation.reality_id not in REALITY_IDS:
            raise ValueError(f"Unknown evaluation reality: {observation.reality_id}")
        if observation.reality_id in indexed:
            raise ValueError(f"Duplicate evaluation reality: {observation.reality_id}")
        _require_digest(observation.evidence_digest, f"Reality {observation.reality_id} evidence")
        for name in ("success", "safety", "reproducibility", "rollback", "novelty", "efficiency", "confidence"):
            _bounded_score(float(getattr(observation, name)), f"Reality {observation.reality_id} {name}")
        indexed[observation.reality_id] = observation
    missing = set(REALITY_IDS).difference(indexed)
    if missing:
        raise ValueError(f"Evaluation is missing required realities: {sorted(missing)}")
    return indexed


def _fitness(observations: Sequence[RealityObservation]) -> FitnessVector:
    promotion = [item for item in observations if item.reality_id in PROMOTION_REALITIES]
    count = len(promotion)
    return FitnessVector(
        success=round(sum(item.success for item in promotion) / count, 6),
        safety=round(min(item.safety for item in promotion), 6),
        reproducibility=round(min(item.reproducibility for item in promotion), 6),
        rollback=round(min(item.rollback for item in promotion), 6),
        novelty=round(sum(item.novelty for item in promotion) / count, 6),
        efficiency=round(sum(item.efficiency for item in promotion) / count, 6),
        confidence=round(min(item.confidence for item in promotion), 6),
    )


def evaluate_sealed_candidate(
    lineage: CandidateLineage,
    observations: Sequence[RealityObservation],
    controls: EvaluationControls,
) -> dict[str, object]:
    indexed = _validated_observations(observations)
    fitness = _fitness(tuple(indexed.values()))
    unchanged = _require_digest(controls.live_tree_before, "Live tree before") == _require_digest(
        controls.live_tree_after, "Live tree after"
    )
    required_reality_pass = all(
        indexed[reality_id].success >= controls.champion_floor
        and indexed[reality_id].safety == 1.0
        and indexed[reality_id].reproducibility == 1.0
        and indexed[reality_id].rollback == 1.0
        and indexed[reality_id].deterministic
        and not indexed[reality_id].contaminated
        for reality_id in PROMOTION_REALITIES
    )
    hard_gates: dict[str, bool] = {
        "staticCourt": controls.static_court_passed,
        "isolatedWorktree": controls.isolated_worktree,
        "deterministicValidation": controls.deterministic_validation_passed,
        "requiredRealities": required_reality_pass,
        "freshHeldoutUncontaminated": not indexed["fresh-heldout"].contaminated,
        "adversarialPassed": indexed["adversarial"].success >= controls.champion_floor,
        "historicalReplayPassed": indexed["historical-replay"].success >= controls.champion_floor,
        "rollbackDrill": controls.rollback_drill_passed,
        "championFloor": fitness.success >= controls.champion_floor,
        "quorum": controls.quorum_passed,
        "liveTreeUnchanged": unchanged,
    }
    replayable = all(item.deterministic for item in indexed.values())
    evaluation_identity = _canonical_digest(
        {
            "candidateDigest": lineage.candidate_digest,
            "hardGates": hard_gates,
            "fitness": asdict(fitness),
            "realities": [asdict(indexed[reality_id]) for reality_id in REALITY_IDS],
        }
    )
    promotion_evidence = PromotionEvidenceV1(
        PROMOTION_EVIDENCE_SCHEMA,
        lineage.candidate_digest,
        evaluation_identity,
        tuple(indexed[reality_id].evidence_digest for reality_id in REALITY_IDS if reality_id in PROMOTION_REALITIES),
        {
            "constitutional": controls.static_court_passed,
            "safety": fitness.safety == 1.0,
            "provenance": all(not indexed[item].contaminated for item in PROMOTION_REALITIES),
            "rollback": controls.rollback_drill_passed and fitness.rollback == 1.0,
            "authority": unchanged,
            "budget": controls.deterministic_validation_passed,
            "reproducibility": replayable and fitness.reproducibility == 1.0,
        },
        1.0,
        fitness.confidence,
        1.0 - fitness.confidence,
        len(PROMOTION_REALITIES),
        2 if controls.quorum_passed else 0,
        len(PROMOTION_REALITIES),
        len(PROMOTION_REALITIES),
        sum(1 for reality_id in PROMOTION_REALITIES if indexed[reality_id].success >= controls.champion_floor)
        / len(PROMOTION_REALITIES),
        max(0.0, fitness.success - controls.champion_floor),
        1.0,
        any(indexed[item].contaminated for item in PROMOTION_REALITIES),
        (),
        controls.rollback_drill_passed,
        True,
        unchanged,
        controls.deterministic_validation_passed,
        unchanged,
        replayable,
    )
    promotion = evaluate_autonomous_promotion(PromotionScopeV1.GENOME, promotion_evidence)
    promotion_eligible = promotion.promoted
    result = {
        "schema": EVALUATION_SCHEMA,
        "candidateDigest": lineage.candidate_digest,
        "lineageDigest": lineage.lineage_digest,
        "mutationLayer": lineage.mutation_layer,
        "pipelineStages": list(PIPELINE_STAGES),
        "hardGates": hard_gates,
        "fitness": asdict(fitness),
        "realities": [asdict(indexed[reality_id]) for reality_id in REALITY_IDS],
        "syntheticFuturePolicy": "FALSIFICATION_ONLY",
        "syntheticFutureUsedForPromotion": False,
        "replayStatus": "CONTENT_IDENTIFIED" if replayable else "NON_REPRODUCIBLE_EXACT",
        "evaluationIdentity": evaluation_identity,
        "promotionStatus": promotion.status,
        "promotionEligible": promotion_eligible,
        "automaticPromotionAuthorized": promotion.automatic_promotion_authorized,
        "operatorReviewRequired": False,
        "activationAuthorized": promotion.promoted_projection_activation_authorized,
        "liveTreeMutationAuthorized": False,
        "promotionDecisionDigest": promotion.decision_digest,
        "promotionFailedGates": list(promotion.failed_gates),
    }
    return result


def dominates(left: Mapping[str, float], right: Mapping[str, float]) -> bool:
    dimensions = ("success", "safety", "reproducibility", "rollback", "novelty", "efficiency", "confidence")
    return all(float(left[item]) >= float(right[item]) for item in dimensions) and any(
        float(left[item]) > float(right[item]) for item in dimensions
    )


def build_quality_diversity_archive(evaluations: Sequence[Mapping[str, object]]) -> dict[str, object]:
    entries = [dict(item) for item in evaluations]
    identities = [str(item.get("evaluationIdentity") or "") for item in entries]
    if any(not item for item in identities) or len(set(identities)) != len(identities):
        raise ValueError("Archive evaluations require unique identities.")
    nondominated: list[str] = []
    for entry in entries:
        fitness = entry.get("fitness")
        if not isinstance(fitness, dict):
            raise ValueError("Archive evaluation requires a fitness vector.")
        if not any(
            other is not entry
            and isinstance(other.get("fitness"), dict)
            and dominates(other["fitness"], fitness)
            for other in entries
        ):
            nondominated.append(str(entry["evaluationIdentity"]))
    eligible = [
        str(item["evaluationIdentity"])
        for item in entries
        if item.get("promotionEligible") is True and str(item["evaluationIdentity"]) in nondominated
    ]
    return {
        "schema": ARCHIVE_SCHEMA,
        "status": "CHAMPIONS_AUTONOMOUSLY_PROMOTED" if eligible else "NO_CHAMPION",
        "entries": entries,
        "preservedCandidateCount": len(entries),
        "failedLineagesPreserved": sum(1 for item in entries if item.get("promotionEligible") is not True),
        "paretoFront": nondominated,
        "championCandidates": eligible,
        "scalarRankingUsed": False,
        "automaticPromotionAuthorized": bool(eligible),
        "operatorPromotionRequired": False,
    }


def evaluate_mutation_operator(ordinary_selection_proven: bool, evidence_digests: Sequence[str]) -> dict[str, object]:
    if not ordinary_selection_proven:
        raise PermissionError("Mutation-operator evaluation requires proven ordinary mutation selection.")
    evidence = tuple(_require_digest(item, "Mutation-operator evidence") for item in evidence_digests)
    if not evidence:
        raise ValueError("Mutation-operator evaluation requires evidence.")
    return {
        "status": "EVALUATION_ALLOWED_AUTONOMOUS_THRESHOLD_GATE_REQUIRED",
        "evidenceDigests": list(evidence),
        "operatorMutationAuthorized": False,
        "operatorPromotionRequired": False,
        "activationAuthorized": False,
    }
