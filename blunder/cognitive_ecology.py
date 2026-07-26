"""Content-addressed genome, memory, reputation, and federation contracts."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Literal, Mapping, Sequence

from blunder.promotion_policy import (
    EVIDENCE_SCHEMA as PROMOTION_EVIDENCE_SCHEMA,
    PromotionEvidenceV1,
    PromotionScopeV1,
    evaluate_autonomous_promotion,
)


GENOME_SCHEMA = "obtuse.blunder.mutation-genome.v1"
ARCHIVE_SCHEMA = "obtuse.blunder.genome-archive.v1"
MEMORY_SCHEMA = "obtuse.blunder.memory-record.v1"
CAPSULE_SCHEMA = "obtuse.blunder.genome-capsule.v1"
FEDERATION_SCHEMA = "obtuse.blunder.federation-envelope.v1"

MemoryTier = Literal["episode", "mission", "capability", "pattern", "principle", "law", "theory", "architecture"]
MEMORY_TIERS: tuple[str, ...] = (
    "episode",
    "mission",
    "capability",
    "pattern",
    "principle",
    "law",
    "theory",
    "architecture",
)


@dataclass(frozen=True)
class MutationGenome:
    schema: str
    prompts: tuple[str, ...]
    context_rules: tuple[str, ...]
    organ_topology: tuple[tuple[str, str], ...]
    tools: tuple[str, ...]
    non_root_policy: tuple[str, ...]
    evaluators: tuple[str, ...]
    compatibility: tuple[str, ...]
    lineage: tuple[str, ...]
    nondeterminism_bound: float
    genome_digest: str


@dataclass(frozen=True)
class GenomeEvaluation:
    genome_digest: str
    model_identity: str
    domain: str
    fitness: tuple[float, ...]
    evidence_digest: str
    reproducible: bool


@dataclass(frozen=True)
class MemoryRecord:
    schema: str
    memory_id: str
    tier: MemoryTier
    claim_digest: str
    evidence_digests: tuple[str, ...]
    independent_sources: int
    domains: tuple[str, ...]
    contradictor_searches: int
    contradictions: tuple[str, ...]
    confidence: float
    uncertainty: float
    created_at: datetime
    refreshed_at: datetime
    expires_at: datetime
    supersedes: tuple[str, ...]
    operator_approved: bool


@dataclass(frozen=True)
class ReputationPosterior:
    domain: str
    alpha: float
    beta: float
    mean: float
    uncertainty: float
    effective_observations: float
    decay_factor: float


@dataclass(frozen=True)
class CapsuleChecks:
    schema_valid: bool
    secrets_absent: bool
    license_compatible: bool
    local_replay_passed: bool
    required_realities_passed: bool


SignatureVerifier = Callable[[bytes, str, str], bool]


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def require_digest(value: str, context: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) != 64 or any(character not in "0123456789ABCDEF" for character in normalized):
        raise ValueError(f"{context} requires a 64-character SHA-256 digest.")
    return normalized


def require_strings(values: Sequence[str], context: str) -> tuple[str, ...]:
    normalized = tuple(value.strip() for value in values)
    if not normalized or any(not value for value in normalized) or len(set(normalized)) != len(normalized):
        raise ValueError(f"{context} requires unique non-empty values.")
    return normalized


def require_score(value: float, context: str) -> float:
    score = float(value)
    if score < 0.0 or score > 1.0:
        raise ValueError(f"{context} must be between 0.0 and 1.0.")
    return score


def build_mutation_genome(
    prompts: Sequence[str],
    context_rules: Sequence[str],
    organ_topology: Sequence[tuple[str, str]],
    tools: Sequence[str],
    non_root_policy: Sequence[str],
    evaluators: Sequence[str],
    compatibility: Sequence[str],
    lineage: Sequence[str],
    nondeterminism_bound: float,
) -> MutationGenome:
    topology = tuple((source.strip(), target.strip()) for source, target in organ_topology)
    if not topology or any(not source or not target for source, target in topology) or len(set(topology)) != len(topology):
        raise ValueError("Genome organ topology requires unique non-empty edges.")
    ancestors = tuple(require_digest(item, "Genome lineage") for item in lineage)
    payload = {
        "schema": GENOME_SCHEMA,
        "prompts": require_strings(prompts, "Genome prompts"),
        "contextRules": require_strings(context_rules, "Genome context rules"),
        "organTopology": topology,
        "tools": require_strings(tools, "Genome tools"),
        "nonRootPolicy": require_strings(non_root_policy, "Genome non-root policy"),
        "evaluators": require_strings(evaluators, "Genome evaluators"),
        "compatibility": require_strings(compatibility, "Genome compatibility"),
        "lineage": ancestors,
        "nondeterminismBound": require_score(nondeterminism_bound, "Genome nondeterminism bound"),
    }
    return MutationGenome(
        schema=GENOME_SCHEMA,
        prompts=payload["prompts"],
        context_rules=payload["contextRules"],
        organ_topology=topology,
        tools=payload["tools"],
        non_root_policy=payload["nonRootPolicy"],
        evaluators=payload["evaluators"],
        compatibility=payload["compatibility"],
        lineage=ancestors,
        nondeterminism_bound=payload["nondeterminismBound"],
        genome_digest=canonical_digest(payload),
    )


def reproduce_genome(genome: MutationGenome, observed_digest: str, divergence: float) -> dict[str, object]:
    divergence_score = require_score(divergence, "Genome reproduction divergence")
    identity_match = require_digest(observed_digest, "Observed genome") == genome.genome_digest
    compatible = identity_match or divergence_score <= genome.nondeterminism_bound
    return {
        "declaredGenome": genome.genome_digest,
        "observedGenome": observed_digest.upper(),
        "divergence": divergence_score,
        "nondeterminismBound": genome.nondeterminism_bound,
        "status": "REPRODUCED_WITHIN_BOUND" if compatible else "REPRODUCTION_MISMATCH",
        "compatible": compatible,
        "activationAuthorized": False,
    }


def build_genome_archive(
    genomes: Sequence[MutationGenome],
    evaluations: Sequence[GenomeEvaluation],
    niches: Mapping[str, Sequence[str]],
    supersessions: Mapping[str, str],
    tombstones: Sequence[str],
) -> dict[str, object]:
    indexed = {genome.genome_digest: genome for genome in genomes}
    if len(indexed) != len(genomes):
        raise ValueError("Genome archive contains duplicate genome identities.")
    evaluation_rows: list[dict[str, object]] = []
    for evaluation in evaluations:
        genome_digest = require_digest(evaluation.genome_digest, "Genome evaluation")
        if genome_digest not in indexed:
            raise ValueError(f"Genome evaluation references an unknown genome: {genome_digest}")
        if not evaluation.fitness or any(score < 0.0 or score > 1.0 for score in evaluation.fitness):
            raise ValueError("Genome evaluation fitness must be a non-empty bounded vector.")
        require_digest(evaluation.evidence_digest, "Genome evaluation evidence")
        evaluation_rows.append(asdict(evaluation))
    niche_rows: dict[str, list[str]] = {}
    for niche, identities in niches.items():
        members = [require_digest(item, f"Genome niche {niche}") for item in identities]
        if any(item not in indexed for item in members):
            raise ValueError(f"Genome niche {niche} references an unknown genome.")
        niche_rows[niche] = members
    supersession_rows = {
        require_digest(prior, "Superseded genome"): require_digest(current, "Superseding genome")
        for prior, current in supersessions.items()
    }
    if any(prior not in indexed or current not in indexed for prior, current in supersession_rows.items()):
        raise ValueError("Genome supersession references an unknown genome.")
    tombstone_rows = tuple(require_digest(item, "Genome tombstone") for item in tombstones)
    if any(item not in indexed for item in tombstone_rows):
        raise ValueError("Genome tombstone references an unknown genome.")
    return {
        "schema": ARCHIVE_SCHEMA,
        "genomes": {identity: asdict(genome) for identity, genome in indexed.items()},
        "evaluations": evaluation_rows,
        "niches": niche_rows,
        "supersessions": supersession_rows,
        "tombstones": list(tombstone_rows),
        "crossModelDomains": sorted({f"{item.model_identity}:{item.domain}" for item in evaluations}),
        "contentAddressed": True,
        "automaticActivationAuthorized": False,
    }


def build_memory_record(
    tier: MemoryTier,
    claim_digest: str,
    evidence_digests: Sequence[str],
    independent_sources: int,
    domains: Sequence[str],
    contradictor_searches: int,
    contradictions: Sequence[str],
    confidence: float,
    uncertainty: float,
    created_at: datetime,
    refreshed_at: datetime,
    expires_at: datetime,
    supersedes: Sequence[str],
    operator_approved: bool,
) -> MemoryRecord:
    if tier not in MEMORY_TIERS:
        raise ValueError(f"Unsupported memory tier: {tier}")
    if any(value.tzinfo is None for value in (created_at, refreshed_at, expires_at)):
        raise ValueError("Memory timestamps must be timezone-aware.")
    if not created_at <= refreshed_at < expires_at:
        raise ValueError("Memory timestamps must satisfy created <= refreshed < expires.")
    evidence = tuple(require_digest(item, "Memory evidence") for item in evidence_digests)
    if not evidence or independent_sources < 1 or contradictor_searches < 0:
        raise ValueError("Memory requires evidence, an independent source, and non-negative contradictor searches.")
    domain_rows = require_strings(domains, "Memory domains")
    contradiction_rows = tuple(require_digest(item, "Memory contradiction") for item in contradictions)
    supersession_rows = tuple(require_digest(item, "Superseded memory") for item in supersedes)
    base = {
        "schema": MEMORY_SCHEMA,
        "tier": tier,
        "claimDigest": require_digest(claim_digest, "Memory claim"),
        "evidenceDigests": evidence,
        "independentSources": independent_sources,
        "domains": domain_rows,
        "contradictorSearches": contradictor_searches,
        "contradictions": contradiction_rows,
        "confidence": require_score(confidence, "Memory confidence"),
        "uncertainty": require_score(uncertainty, "Memory uncertainty"),
        "createdAt": created_at.astimezone(timezone.utc).isoformat(),
        "refreshedAt": refreshed_at.astimezone(timezone.utc).isoformat(),
        "expiresAt": expires_at.astimezone(timezone.utc).isoformat(),
        "supersedes": supersession_rows,
        "operatorApproved": operator_approved,
    }
    return MemoryRecord(
        schema=MEMORY_SCHEMA,
        memory_id=canonical_digest(base),
        tier=tier,
        claim_digest=base["claimDigest"],
        evidence_digests=evidence,
        independent_sources=independent_sources,
        domains=domain_rows,
        contradictor_searches=contradictor_searches,
        contradictions=contradiction_rows,
        confidence=base["confidence"],
        uncertainty=base["uncertainty"],
        created_at=created_at,
        refreshed_at=refreshed_at,
        expires_at=expires_at,
        supersedes=supersession_rows,
        operator_approved=operator_approved,
    )


def memory_transition(record: MemoryRecord, target_tier: MemoryTier, observed_at: datetime) -> dict[str, object]:
    if observed_at.tzinfo is None:
        raise ValueError("Memory transition time must be timezone-aware.")
    if target_tier not in MEMORY_TIERS:
        raise ValueError(f"Unsupported target memory tier: {target_tier}")
    current_index = MEMORY_TIERS.index(record.tier)
    target_index = MEMORY_TIERS.index(target_tier)
    if abs(target_index - current_index) > 1:
        raise ValueError("Memory transitions may move only one tier at a time.")
    age_days = max(0.0, (observed_at - record.created_at).total_seconds() / 86_400.0)
    expired = observed_at >= record.expires_at
    thresholds = {
        "minimumEvidence": target_index + 1,
        "minimumIndependentSources": max(1, target_index),
        "minimumDomains": 1 if target_index < 4 else 2,
        "minimumContradictorSearches": 0 if target_index < 3 else target_index - 2,
        "minimumSurvivalDays": target_index * 7,
        "autonomousThresholdGateRequired": target_index > current_index,
    }
    gates = {
        "fresh": not expired,
        "evidence": len(record.evidence_digests) >= thresholds["minimumEvidence"],
        "independence": record.independent_sources >= thresholds["minimumIndependentSources"],
        "crossDomain": len(record.domains) >= thresholds["minimumDomains"],
        "contradictorSearch": record.contradictor_searches >= thresholds["minimumContradictorSearches"],
        "timeSurvival": age_days >= thresholds["minimumSurvivalDays"],
        "uncertaintyVisible": record.uncertainty <= max(0.1, 0.8 - target_index * 0.08),
        "contradictionFree": not record.contradictions,
    }
    direction = "PROMOTE" if target_index > current_index else "DEMOTE" if target_index < current_index else "RETAIN"
    promotion = None
    if direction == "PROMOTE":
        evaluation_identity = canonical_digest(
            {
                "memoryId": record.memory_id,
                "targetTier": target_tier,
                "observedAt": observed_at.astimezone(timezone.utc).isoformat(),
                "gates": gates,
                "thresholds": thresholds,
            }
        )
        promotion = evaluate_autonomous_promotion(
            PromotionScopeV1.MEMORY,
            PromotionEvidenceV1(
                PROMOTION_EVIDENCE_SCHEMA,
                record.memory_id,
                evaluation_identity,
                record.evidence_digests,
                {
                    "constitutional": True,
                    "safety": not expired,
                    "provenance": bool(record.evidence_digests),
                    "rollback": True,
                    "authority": True,
                    "budget": True,
                    "reproducibility": gates["contradictorSearch"] and gates["contradictionFree"],
                },
                1.0 if all(gates.values()) else 0.0,
                record.confidence,
                record.uncertainty,
                record.independent_sources,
                1,
                len(record.domains),
                len(record.evidence_digests),
                record.confidence,
                0.0,
                1.0,
                False,
                record.contradictions,
                True,
                True,
                True,
                True,
                True,
                gates["contradictorSearch"] and gates["contradictionFree"],
            ),
        )
    accepted = promotion.promoted if promotion is not None else True
    if expired or record.contradictions:
        direction = "DEMOTE_OR_EXPIRE"
        accepted = True
    return {
        "memoryId": record.memory_id,
        "fromTier": record.tier,
        "targetTier": target_tier,
        "direction": direction,
        "status": promotion.status if promotion is not None else "TRANSITION_EXPLAINED_REVERSIBLE",
        "gates": gates,
        "thresholds": thresholds,
        "ageDays": round(age_days, 6),
        "confidence": record.confidence,
        "uncertainty": record.uncertainty,
        "supersedes": list(record.supersedes),
        "reversible": True,
        "authorityGranted": False,
        "automaticPromotionAuthorized": promotion.automatic_promotion_authorized if promotion is not None else False,
        "promotedProjectionActivationAuthorized": (
            promotion.promoted_projection_activation_authorized if promotion is not None else False
        ),
        "operatorPromotionRequired": False,
        "operatorApprovedCompatibilityFieldIgnored": record.operator_approved,
        "promotionDecisionDigest": promotion.decision_digest if promotion is not None else None,
    }


def reputation_posterior(domain: str, successes: int, failures: int, age_days: float, half_life_days: float) -> ReputationPosterior:
    if successes < 0 or failures < 0 or age_days < 0.0 or half_life_days <= 0.0:
        raise ValueError("Reputation counts and age must be non-negative and half-life positive.")
    decay = math.pow(0.5, age_days / half_life_days)
    alpha = 1.0 + successes * decay
    beta = 1.0 + failures * decay
    total = alpha + beta
    mean = alpha / total
    variance = (alpha * beta) / (total * total * (total + 1.0))
    return ReputationPosterior(
        domain=domain.strip() or "unknown",
        alpha=round(alpha, 6),
        beta=round(beta, 6),
        mean=round(mean, 6),
        uncertainty=round(math.sqrt(variance), 6),
        effective_observations=round((successes + failures) * decay, 6),
        decay_factor=round(decay, 6),
    )


def bounded_schedule(
    candidates: Sequence[tuple[str, ReputationPosterior]],
    constitutional_gates_passed: bool,
    budget_gates_passed: bool,
    maximum_candidates: int,
) -> dict[str, object]:
    if maximum_candidates < 1:
        raise ValueError("Scheduler maximum candidates must be positive.")
    eligible = constitutional_gates_passed and budget_gates_passed
    ranked = sorted(candidates, key=lambda item: (-(item[1].mean - item[1].uncertainty), item[0]))
    selected = [identity for identity, _posterior in ranked[:maximum_candidates]] if eligible else []
    return {
        "status": "BOUNDED_SELECTION" if eligible else "HARD_GATE_BLOCKED",
        "selected": selected,
        "ranked": [
            {"candidate": identity, "domain": posterior.domain, "mean": posterior.mean, "uncertainty": posterior.uncertainty}
            for identity, posterior in ranked
        ],
        "maximumCandidates": maximum_candidates,
        "reputationCanBypassGates": False,
        "creditCanPurchaseAuthority": False,
        "authorityGranted": False,
    }


def capsule_signing_payload(capsule: Mapping[str, object]) -> bytes:
    excluded = {"signature", "signatureAlgorithm", "signerIdentity"}
    payload = {key: value for key, value in capsule.items() if key not in excluded}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def verify_and_quarantine_capsule(
    capsule: Mapping[str, object],
    checks: CapsuleChecks,
    verifier: SignatureVerifier,
) -> dict[str, object]:
    signature = str(capsule.get("signature") or "")
    algorithm = str(capsule.get("signatureAlgorithm") or "")
    signer = str(capsule.get("signerIdentity") or "")
    reasons: list[str] = []
    if capsule.get("schema") != CAPSULE_SCHEMA:
        reasons.append("SCHEMA_INVALID")
    if not signature or not algorithm or not signer:
        reasons.append("SIGNATURE_MISSING")
    elif not verifier(capsule_signing_payload(capsule), signature, signer):
        reasons.append("SIGNATURE_INVALID_OR_TAMPERED")
    if capsule.get("authorityTransferred") is not False:
        reasons.append("AUTHORITY_TRANSFER_FIELD_INVALID")
    forbidden = {"credentials", "privateMemory", "approval", "activation"}.intersection(capsule)
    if forbidden:
        reasons.append(f"FORBIDDEN_TRANSFER_FIELDS:{','.join(sorted(forbidden))}")
    check_rows = asdict(checks)
    reasons.extend(name.upper() for name, passed in check_rows.items() if not passed)
    accepted = not reasons
    return {
        "schema": "obtuse.blunder.genome-capsule-admission.v1",
        "capsuleDigest": canonical_digest(dict(capsule)),
        "status": "QUARANTINED_ELIGIBLE_FOR_LOCAL_REVIEW" if accepted else "REJECTED",
        "reasons": reasons,
        "checks": check_rows,
        "quarantined": True,
        "localReplayRequired": True,
        "requiredRealitiesMustRerun": True,
        "authorityTransferred": False,
        "credentialsTransferred": False,
        "privateMemoryTransferred": False,
        "approvalTransferred": False,
        "automaticActivation": False,
    }


def federation_envelope(
    peer_identity: str,
    capability_card_digest: str,
    task_digest: str,
    artifact_digests: Sequence[str],
    local_contracts_stable: bool,
) -> dict[str, object]:
    if not local_contracts_stable:
        raise PermissionError("Federation adapter requires stable local contracts.")
    artifacts = tuple(require_digest(item, "Federated artifact") for item in artifact_digests)
    envelope = {
        "schema": FEDERATION_SCHEMA,
        "adapterCompatibility": "A2A_DISCOVERY_TASK_ARTIFACT_SUBSET",
        "toolResourceBoundary": "MCP",
        "peerIdentity": peer_identity.strip(),
        "capabilityCardDigest": require_digest(capability_card_digest, "Capability card"),
        "taskDigest": require_digest(task_digest, "Federated task"),
        "artifactDigests": artifacts,
        "credentials": None,
        "privateMemory": None,
        "approval": None,
        "authorityTransferred": False,
        "automaticActivation": False,
        "quarantineRequired": True,
    }
    if not envelope["peerIdentity"] or not artifacts:
        raise ValueError("Federation envelope requires a peer and artifacts.")
    return envelope | {"envelopeDigest": canonical_digest(envelope)}
