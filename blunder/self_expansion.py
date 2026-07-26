"""Governed capability-gap, organ-design, and meta-evolution contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, Mapping, Sequence

from blunder.cognitive_ecology import canonical_digest, require_digest, require_score, require_strings
from blunder.promotion_policy import (
    EVIDENCE_SCHEMA as PROMOTION_EVIDENCE_SCHEMA,
    PromotionEvidenceV1,
    PromotionScopeV1,
    evaluate_autonomous_promotion,
)


GAP_SCHEMA = "obtuse.blunder.organ-need-case.v1"
ORGAN_SCHEMA = "obtuse.blunder.organ-design.v1"
EXPANSION_SCHEMA = "obtuse.blunder.organ-expansion-decision.v1"
RECOVERY_SCHEMA = "obtuse.blunder.minimal-recovery-governor.v1"

GapKind = Literal[
    "failure-cluster",
    "bottleneck",
    "high-entropy",
    "unresolved-potential",
    "expensive-review",
    "missing-evidence-capability",
]

GAP_KINDS: tuple[str, ...] = (
    "failure-cluster",
    "bottleneck",
    "high-entropy",
    "unresolved-potential",
    "expensive-review",
    "missing-evidence-capability",
)

META_EVOLUTION_LAYERS: tuple[str, ...] = (
    "mutation-operators",
    "parent-selection",
    "archive-policy",
    "fitness-views",
    "simulation-selection",
    "non-root-governance-proposals",
)

ROOT_INVARIANT_MARKERS: tuple[str, ...] = (
    "root-authority",
    "constitution-activation",
    "credential-access",
    "operator-impersonation",
    "automatic-promotion",
)


@dataclass(frozen=True)
class GapSignal:
    kind: GapKind
    cluster_id: str
    measured_value: float
    evidence_digest: str
    observed_context: str


@dataclass(frozen=True)
class OrganNeedCase:
    schema: str
    case_digest: str
    kind: GapKind
    cluster_id: str
    observation_count: int
    evidence_digests: tuple[str, ...]
    existing_organs_tried: tuple[str, ...]
    counterfactual_evidence: tuple[str, ...]
    predeclared_fitness_dimension: str
    baseline_value: float
    target_value: float


@dataclass(frozen=True)
class OrganBudget:
    maximum_seconds: int
    maximum_tokens: int
    maximum_cost_usd: float
    maximum_provider_calls: int


@dataclass(frozen=True)
class OrganDesign:
    schema: str
    name: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    tools: tuple[str, ...]
    non_root_policy: tuple[str, ...]
    budget: OrganBudget
    state_contract: tuple[str, ...]
    evaluators: tuple[str, ...]
    rollback_contract: tuple[str, ...]
    observability: tuple[str, ...]
    design_digest: str


@dataclass(frozen=True)
class TransferMeasurement:
    model_family: str
    task_domain: str
    fitness_value: float
    cost_value: float
    hard_gates_passed: bool
    evidence_digest: str


@dataclass(frozen=True)
class RecoveryGovernor:
    schema: str
    known_good_genome_digest: str
    baseline_scheduler_digest: str
    baseline_evolution_engine_digest: str
    invariant_digest: str


def mine_organ_need_case(
    signals: Sequence[GapSignal],
    existing_organs_tried: Sequence[str],
    counterfactual_evidence: Sequence[str],
    predeclared_fitness_dimension: str,
    baseline_value: float,
    target_value: float,
) -> OrganNeedCase:
    if len(signals) < 3:
        raise ValueError("Organ need requires at least three repeated measured observations.")
    kinds = {signal.kind for signal in signals}
    clusters = {signal.cluster_id.strip() for signal in signals}
    if len(kinds) != 1 or len(clusters) != 1 or "" in clusters:
        raise ValueError("Organ need signals must describe one measured gap cluster.")
    kind = next(iter(kinds))
    if kind not in GAP_KINDS:
        raise ValueError(f"Unsupported gap kind: {kind}")
    if any(signal.measured_value < 0.0 for signal in signals):
        raise ValueError("Gap measurements must be non-negative.")
    evidence = tuple(require_digest(signal.evidence_digest, "Gap evidence") for signal in signals)
    if len(set(evidence)) != len(evidence):
        raise ValueError("Repeated gap observations require independent evidence identities.")
    organs = require_strings(existing_organs_tried, "Existing organs tried")
    counterfactuals = tuple(require_digest(item, "Counterfactual evidence") for item in counterfactual_evidence)
    if len(counterfactuals) < 2:
        raise ValueError("Organ need requires at least two counterfactual evidence records.")
    if target_value <= baseline_value:
        raise ValueError("Organ need target must improve the predeclared baseline.")
    base = {
        "schema": GAP_SCHEMA,
        "kind": kind,
        "clusterId": next(iter(clusters)),
        "observationCount": len(signals),
        "evidenceDigests": evidence,
        "existingOrgansTried": organs,
        "counterfactualEvidence": counterfactuals,
        "predeclaredFitnessDimension": predeclared_fitness_dimension.strip(),
        "baselineValue": baseline_value,
        "targetValue": target_value,
    }
    if not base["predeclaredFitnessDimension"]:
        raise ValueError("Organ need requires a predeclared fitness dimension.")
    return OrganNeedCase(
        schema=GAP_SCHEMA,
        case_digest=canonical_digest(base),
        kind=kind,
        cluster_id=base["clusterId"],
        observation_count=len(signals),
        evidence_digests=evidence,
        existing_organs_tried=organs,
        counterfactual_evidence=counterfactuals,
        predeclared_fitness_dimension=base["predeclaredFitnessDimension"],
        baseline_value=baseline_value,
        target_value=target_value,
    )


def build_organ_design(
    name: str,
    inputs: Sequence[str],
    outputs: Sequence[str],
    tools: Sequence[str],
    non_root_policy: Sequence[str],
    budget: OrganBudget,
    state_contract: Sequence[str],
    evaluators: Sequence[str],
    rollback_contract: Sequence[str],
    observability: Sequence[str],
) -> OrganDesign:
    if budget.maximum_seconds < 1 or budget.maximum_tokens < 1 or budget.maximum_cost_usd < 0.0 or budget.maximum_provider_calls < 0:
        raise ValueError("Organ budget is invalid.")
    policy = require_strings(non_root_policy, "Organ non-root policy")
    all_terms = tuple(item.casefold() for item in (*tools, *policy, *outputs))
    violations = sorted(marker for marker in ROOT_INVARIANT_MARKERS if any(marker in term for term in all_terms))
    if violations:
        raise PermissionError(f"Organ design intersects root invariants: {violations}")
    payload = {
        "schema": ORGAN_SCHEMA,
        "name": name.strip(),
        "inputs": require_strings(inputs, "Organ inputs"),
        "outputs": require_strings(outputs, "Organ outputs"),
        "tools": require_strings(tools, "Organ tools"),
        "nonRootPolicy": policy,
        "budget": asdict(budget),
        "stateContract": require_strings(state_contract, "Organ state contract"),
        "evaluators": require_strings(evaluators, "Organ evaluators"),
        "rollbackContract": require_strings(rollback_contract, "Organ rollback contract"),
        "observability": require_strings(observability, "Organ observability"),
    }
    if not payload["name"]:
        raise ValueError("Organ design requires a name.")
    return OrganDesign(
        schema=ORGAN_SCHEMA,
        name=payload["name"],
        inputs=payload["inputs"],
        outputs=payload["outputs"],
        tools=payload["tools"],
        non_root_policy=policy,
        budget=budget,
        state_contract=payload["stateContract"],
        evaluators=payload["evaluators"],
        rollback_contract=payload["rollbackContract"],
        observability=payload["observability"],
        design_digest=canonical_digest(payload),
    )


def expansion_candidates(need: OrganNeedCase, organ: OrganDesign, configuration_change: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    return (
        {
            "kind": "typed-new-organ",
            "candidateDigest": organ.design_digest,
            "needCaseDigest": need.case_digest,
            "design": asdict(organ),
            "activationAuthorized": False,
        },
        {
            "kind": "simpler-configuration-control",
            "candidateDigest": canonical_digest(dict(configuration_change)),
            "needCaseDigest": need.case_digest,
            "configuration": dict(configuration_change),
            "activationAuthorized": False,
        },
        {
            "kind": "no-new-organ-control",
            "candidateDigest": canonical_digest({"need": need.case_digest, "control": "no-change"}),
            "needCaseDigest": need.case_digest,
            "activationAuthorized": False,
        },
    )


def evaluate_organ_expansion(
    need: OrganNeedCase,
    organ: OrganDesign,
    sealed_evaluation: Mapping[str, object],
    transfer_measurements: Sequence[TransferMeasurement],
    baseline_cost: float,
    prior_state_digest: str,
    disabled_state_digest: str,
) -> dict[str, object]:
    models = {item.model_family for item in transfer_measurements}
    domains = {item.task_domain for item in transfer_measurements}
    for measurement in transfer_measurements:
        require_score(measurement.fitness_value, "Transfer fitness")
        if measurement.cost_value < 0.0:
            raise ValueError("Transfer cost must be non-negative.")
        require_digest(measurement.evidence_digest, "Transfer evidence")
    mean_fitness = sum(item.fitness_value for item in transfer_measurements) / len(transfer_measurements) if transfer_measurements else 0.0
    mean_cost = sum(item.cost_value for item in transfer_measurements) / len(transfer_measurements) if transfer_measurements else float("inf")
    exact_disable = require_digest(prior_state_digest, "Prior state") == require_digest(disabled_state_digest, "Disabled state")
    gates: dict[str, bool] = {
        "sealedEpochFourPipeline": sealed_evaluation.get("promotionEligible") is True
        and sealed_evaluation.get("liveTreeMutationAuthorized") is False
        and isinstance(sealed_evaluation.get("realities"), list)
        and len(sealed_evaluation["realities"]) == 7,
        "twoModelFamilies": len(models) >= 2,
        "twoTaskDomains": len(domains) >= 2,
        "predeclaredFitnessImproved": mean_fitness >= need.target_value,
        "noHardGateRegression": bool(transfer_measurements) and all(item.hard_gates_passed for item in transfer_measurements),
        "noCostDisplacement": mean_cost <= baseline_cost,
        "exactDisableRollback": exact_disable,
    }
    evaluation_identity = canonical_digest(
        {
            "needCaseDigest": need.case_digest,
            "organDesignDigest": organ.design_digest,
            "gates": gates,
            "measurements": [asdict(item) for item in transfer_measurements],
        }
    )
    evidence_digests = tuple(
        dict.fromkeys((*need.evidence_digests, *(item.evidence_digest for item in transfer_measurements)))
    )
    promotion = evaluate_autonomous_promotion(
        PromotionScopeV1.ORGAN,
        PromotionEvidenceV1(
            PROMOTION_EVIDENCE_SCHEMA,
            organ.design_digest,
            evaluation_identity,
            evidence_digests,
            {
                "constitutional": gates["sealedEpochFourPipeline"],
                "safety": gates["noHardGateRegression"],
                "provenance": bool(evidence_digests),
                "rollback": gates["exactDisableRollback"],
                "authority": gates["sealedEpochFourPipeline"],
                "budget": gates["noCostDisplacement"],
                "reproducibility": gates["twoModelFamilies"] and gates["twoTaskDomains"],
            },
            1.0 if all(gates.values()) else 0.0,
            min((item.fitness_value for item in transfer_measurements), default=0.0),
            1.0 - min((item.fitness_value for item in transfer_measurements), default=0.0),
            len(evidence_digests),
            len(models),
            len(domains),
            len(transfer_measurements),
            sum(1 for item in transfer_measurements if item.fitness_value >= need.target_value)
            / len(transfer_measurements)
            if transfer_measurements
            else 0.0,
            max(0.0, (mean_fitness - need.baseline_value) / need.baseline_value)
            if need.baseline_value > 0.0
            else 0.0,
            mean_cost / baseline_cost if baseline_cost > 0.0 and mean_cost != float("inf") else 1_000.0,
            False,
            (),
            gates["exactDisableRollback"],
            bool(evidence_digests),
            gates["sealedEpochFourPipeline"],
            gates["noCostDisplacement"],
            exact_disable,
            gates["twoModelFamilies"] and gates["twoTaskDomains"],
        ),
    )
    selected = promotion.promoted
    return {
        "schema": EXPANSION_SCHEMA,
        "needCaseDigest": need.case_digest,
        "organDesignDigest": organ.design_digest,
        "status": promotion.status,
        "gates": gates,
        "modelFamilies": sorted(models),
        "taskDomains": sorted(domains),
        "meanFitness": round(mean_fitness, 6),
        "meanCost": round(mean_cost, 6) if mean_cost != float("inf") else None,
        "predeclaredDimension": need.predeclared_fitness_dimension,
        "durableParetoValue": selected,
        "activationAuthorized": promotion.promoted_projection_activation_authorized,
        "operatorReviewRequired": False,
        "automaticPromotionAuthorized": promotion.automatic_promotion_authorized,
        "promotionDecisionDigest": promotion.decision_digest,
        "promotionFailedGates": list(promotion.failed_gates),
        "constitutionProposalActivationAuthorized": False,
    }


def meta_evolution_gate(
    layer: str,
    ordinary_selection_proven: bool,
    higher_order_holdout_passed: bool,
    independent_evaluation_passed: bool,
    evidence_digests: Sequence[str],
    independent_models: int,
    domains: int,
    holdout_trials: int,
    confidence: float,
    uncertainty: float,
    rollback_proven: bool,
) -> dict[str, object]:
    if layer not in META_EVOLUTION_LAYERS:
        raise ValueError(f"Unsupported meta-evolution layer: {layer}")
    evidence = tuple(require_digest(item, "Meta-evolution evidence") for item in evidence_digests)
    evaluation_identity = canonical_digest(
        {
            "layer": layer,
            "ordinarySelectionProven": ordinary_selection_proven,
            "higherOrderHoldoutPassed": higher_order_holdout_passed,
            "independentEvaluationPassed": independent_evaluation_passed,
            "evidenceDigests": evidence,
        }
    )
    promotion = evaluate_autonomous_promotion(
        PromotionScopeV1.STRATEGY,
        PromotionEvidenceV1(
            PROMOTION_EVIDENCE_SCHEMA,
            canonical_digest({"metaEvolutionLayer": layer}),
            evaluation_identity,
            evidence,
            {
                "constitutional": ordinary_selection_proven,
                "safety": independent_evaluation_passed,
                "provenance": bool(evidence),
                "rollback": rollback_proven,
                "authority": True,
                "budget": True,
                "reproducibility": higher_order_holdout_passed,
            },
            1.0 if ordinary_selection_proven and higher_order_holdout_passed and independent_evaluation_passed else 0.0,
            require_score(confidence, "Meta-evolution confidence"),
            require_score(uncertainty, "Meta-evolution uncertainty"),
            len(evidence),
            independent_models,
            domains,
            holdout_trials,
            1.0 if higher_order_holdout_passed else 0.0,
            0.01 if higher_order_holdout_passed else 0.0,
            1.0,
            False,
            (),
            rollback_proven,
            bool(evidence),
            True,
            True,
            True,
            independent_evaluation_passed,
        ),
    )
    return {
        "layer": layer,
        "status": promotion.status,
        "ordinarySelectionProven": ordinary_selection_proven,
        "higherOrderHoldoutPassed": higher_order_holdout_passed,
        "independentEvaluationPassed": independent_evaluation_passed,
        "automaticPromotionAuthorized": promotion.automatic_promotion_authorized,
        "operatorPromotionRequired": False,
        "promotionDecisionDigest": promotion.decision_digest,
        "promotionFailedGates": list(promotion.failed_gates),
        "rootInvariantMutationAuthorized": False,
        "activationAuthorized": promotion.promoted_projection_activation_authorized,
    }


def build_recovery_governor(
    known_good_genome_digest: str,
    baseline_scheduler_digest: str,
    baseline_evolution_engine_digest: str,
) -> RecoveryGovernor:
    base = {
        "schema": RECOVERY_SCHEMA,
        "knownGoodGenomeDigest": require_digest(known_good_genome_digest, "Known-good genome"),
        "baselineSchedulerDigest": require_digest(baseline_scheduler_digest, "Baseline scheduler"),
        "baselineEvolutionEngineDigest": require_digest(baseline_evolution_engine_digest, "Baseline evolution engine"),
        "immutable": True,
    }
    return RecoveryGovernor(
        schema=RECOVERY_SCHEMA,
        known_good_genome_digest=base["knownGoodGenomeDigest"],
        baseline_scheduler_digest=base["baselineSchedulerDigest"],
        baseline_evolution_engine_digest=base["baselineEvolutionEngineDigest"],
        invariant_digest=canonical_digest(base),
    )


def invoke_recovery_governor(
    governor: RecoveryGovernor,
    mission_state_digest: str,
    disable_evolved_schedulers: bool,
    restore_known_good_genome: bool,
    rollback_evolution_engine: bool,
) -> dict[str, object]:
    if not disable_evolved_schedulers or not restore_known_good_genome:
        raise PermissionError("Recovery requires scheduler disablement and known-good genome restoration.")
    mission = require_digest(mission_state_digest, "Mission state")
    return {
        "status": "RECOVERY_BOUND",
        "governorInvariantDigest": governor.invariant_digest,
        "evolvedSchedulersEnabled": False,
        "activeGenomeDigest": governor.known_good_genome_digest,
        "activeSchedulerDigest": governor.baseline_scheduler_digest,
        "activeEvolutionEngineDigest": governor.baseline_evolution_engine_digest if rollback_evolution_engine else None,
        "evolutionEngineRolledBackIndependently": rollback_evolution_engine,
        "missionStateBefore": mission,
        "missionStateAfter": mission,
        "missionStateChanged": False,
        "rootInvariantMutationAuthorized": False,
    }
