"""Deterministic, authority-bounded task-organism compilation for Blunder."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

from blunder.constitutional_kernel import canonical_sha256, format_utc_timestamp, load_runtime_constitution


ORGANISM_SCHEMA = "obtuse.blunder.organism-spec.v1"
ORGAN_ROLES: tuple[str, ...] = (
    "governor",
    "researcher",
    "builder",
    "shadow",
    "critic",
    "validator",
    "historian",
    "evolution-observer",
)
ARCHETYPES: tuple[str, ...] = (
    "refactor",
    "defect-repair",
    "research",
    "architecture",
    "product",
    "recovery",
)
LIFECYCLE_STATES: tuple[str, ...] = (
    "PROPOSED",
    "COMPILED",
    "ADMITTED",
    "BORN",
    "ACTIVE",
    "CHALLENGED",
    "VALIDATED",
    "DISSOLVED",
    "KNOWLEDGE_EXTRACTED",
)


@dataclass(frozen=True)
class OrganismBudget:
    maximum_seconds: int
    maximum_provider_calls: int
    maximum_tokens: int
    maximum_cost_usd: float


@dataclass(frozen=True)
class OrganBudget:
    maximum_seconds: int
    maximum_provider_calls: int
    maximum_tokens: int
    maximum_cost_usd: float


@dataclass(frozen=True)
class OrganSpec:
    organ_id: str
    role: str
    depends_on: tuple[str, ...]
    claimed_paths: tuple[str, ...]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    evidence_contract: tuple[str, ...]
    budget: OrganBudget
    context_identity: str
    authority: Mapping[str, object]


@dataclass(frozen=True)
class EvidenceClaim:
    claim_id: str
    organ_id: str
    kind: str
    content_digest: str
    artifact_digest: str | None
    confidence: float
    observed_at: datetime
    eligible_for_knowledge: bool


_ROLE_INPUTS: Mapping[str, tuple[str, ...]] = {
    "governor": ("task", "constitution", "budget", "repository-graph"),
    "researcher": ("task", "repository-graph", "evidence-needs"),
    "builder": ("admitted-plan", "claimed-paths", "research-claims"),
    "shadow": ("admitted-plan", "risk-model", "research-claims"),
    "critic": ("candidate-claims", "shadow-counterexamples", "acceptance-contract"),
    "validator": ("candidate-artifacts", "critic-findings", "validation-contract"),
    "historian": ("lifecycle-events", "validated-evidence", "resource-charges"),
    "evolution-observer": ("outcome", "fitness-contract", "lineage-context"),
}
_ROLE_OUTPUTS: Mapping[str, tuple[str, ...]] = {
    "governor": ("admission-decision", "bounded-plan"),
    "researcher": ("content-addressed-research-claims",),
    "builder": ("candidate-artifacts", "change-manifest"),
    "shadow": ("counterexamples", "authority-challenges"),
    "critic": ("dissent-record", "required-checks"),
    "validator": ("validation-evidence", "verdict"),
    "historian": ("eligible-evidence-set", "replay-manifest"),
    "evolution-observer": ("non-authoritative-fitness-observation",),
}


def classify_archetype(task: str) -> str:
    normalized = task.lower()
    categories = (
        ("recovery", ("recover", "rollback", "restore", "corrupt")),
        ("defect-repair", ("fix", "bug", "defect", "failure", "repair")),
        ("refactor", ("refactor", "simplify", "restructure", "cleanup")),
        ("research", ("research", "investigate", "discover", "analyze")),
        ("architecture", ("architecture", "design system", "boundary", "protocol")),
        ("product", ("product", "interface", "experience", "feature")),
    )
    return next((name for name, terms in categories if any(term in normalized for term in terms)), "product")


def _validate_budget(budget: OrganismBudget) -> None:
    if budget.maximum_seconds < 30 or budget.maximum_provider_calls < 0:
        raise ValueError("Organism time must be at least 30 seconds and provider calls cannot be negative.")
    if budget.maximum_tokens < 0 or budget.maximum_cost_usd < 0:
        raise ValueError("Organism token and cost budgets cannot be negative.")


def _roles_for_archetype(archetype: str) -> tuple[str, ...]:
    if archetype not in ARCHETYPES:
        raise ValueError(f"Unsupported organism archetype: {archetype}")
    if archetype == "research":
        return ("governor", "researcher", "shadow", "critic", "validator", "historian", "evolution-observer")
    return ORGAN_ROLES


def _dependencies(role: str, organ_ids: Mapping[str, str]) -> tuple[str, ...]:
    governor = organ_ids["governor"]
    researcher = organ_ids["researcher"]
    shadow = organ_ids["shadow"]
    critic = organ_ids["critic"]
    if role == "governor":
        return ()
    if role == "researcher":
        return (governor,)
    if role in {"builder", "shadow"}:
        return (researcher,)
    if role == "critic":
        return (researcher, shadow)
    if role == "validator":
        candidate = [critic, shadow]
        if "builder" in organ_ids:
            candidate.append(organ_ids["builder"])
        return tuple(sorted(candidate))
    if role == "historian":
        return (organ_ids["validator"],)
    if role == "evolution-observer":
        return (organ_ids["historian"],)
    raise ValueError(f"Unsupported organ role: {role}")


def _organ_budget(total: OrganismBudget, index: int, count: int) -> OrganBudget:
    seconds_base, seconds_extra = divmod(total.maximum_seconds, count)
    tokens_base, tokens_extra = divmod(total.maximum_tokens, count)
    calls_base, calls_extra = divmod(total.maximum_provider_calls, count)
    cost = total.maximum_cost_usd / count
    return OrganBudget(
        seconds_base + (1 if index < seconds_extra else 0),
        calls_base + (1 if index < calls_extra else 0),
        tokens_base + (1 if index < tokens_extra else 0),
        round(cost, 8),
    )


def _authority(role: str, shadow_mode: bool) -> dict[str, object]:
    constitution = load_runtime_constitution()
    builder = role == "builder"
    return {
        "role": role,
        "scopes": ["repository:read", "evidence:write"] + (["worktree:bounded-source-mutation"] if builder else []),
        "worktreeRequired": builder,
        "shadowMode": shadow_mode,
        "activation": False,
        "integration": False,
        "commit": False,
        "push": False,
        "preservedDenials": list(constitution.immutable_denials),
    }


def _organ_to_dict(organ: OrganSpec) -> dict[str, object]:
    return {
        "id": organ.organ_id,
        "role": organ.role,
        "dependsOn": list(organ.depends_on),
        "claimedPaths": list(organ.claimed_paths),
        "inputs": list(organ.inputs),
        "outputs": list(organ.outputs),
        "evidenceContract": list(organ.evidence_contract),
        "budget": {
            "maximumSeconds": organ.budget.maximum_seconds,
            "maximumProviderCalls": organ.budget.maximum_provider_calls,
            "maximumTokens": organ.budget.maximum_tokens,
            "maximumCostUsd": organ.budget.maximum_cost_usd,
        },
        "contextIdentity": organ.context_identity,
        "authority": dict(organ.authority),
    }


def _acyclic(organs: Sequence[OrganSpec]) -> bool:
    remaining = {organ.organ_id: set(organ.depends_on) for organ in organs}
    resolved: set[str] = set()
    while remaining:
        ready = sorted(identifier for identifier, dependencies in remaining.items() if dependencies.issubset(resolved))
        if not ready:
            return False
        for identifier in ready:
            resolved.add(identifier)
            del remaining[identifier]
    return True


def analyze_organ_specs(organs: Sequence[OrganSpec]) -> dict[str, object]:
    identifiers = [organ.organ_id for organ in organs]
    duplicate_ids = sorted({identifier for identifier in identifiers if identifiers.count(identifier) > 1})
    known_ids = set(identifiers)
    missing_dependencies = sorted({item for organ in organs for item in organ.depends_on if item not in known_ids})
    claim_owners: dict[str, list[str]] = {}
    for organ in organs:
        for path in organ.claimed_paths:
            claim_owners.setdefault(path, []).append(organ.organ_id)
    conflicts = {path: sorted(owners) for path, owners in sorted(claim_owners.items()) if len(owners) > 1}
    distinct_contexts = len({organ.context_identity for organ in organs}) == len(organs)
    acyclic = not duplicate_ids and _acyclic(organs)
    return {
        "acyclic": acyclic,
        "duplicateOrganIds": duplicate_ids,
        "missingDependencies": missing_dependencies,
        "claimedPathConflicts": conflicts,
        "distinctContexts": distinct_contexts,
        "admissible": acyclic and not missing_dependencies and not conflicts and distinct_contexts,
    }


def compile_organism(
    archetype: str,
    task: str,
    repository_graph_fingerprint: str,
    target_paths: Sequence[str],
    risk: str,
    uncertainty: str,
    evidence_needs: Sequence[str],
    budget: OrganismBudget,
    provider_model: str | None,
    shadow_mode: bool,
) -> dict[str, object]:
    """Compile a deterministic bounded DAG; this function launches no work."""

    normalized_task = task.strip()
    if len(normalized_task) < 3:
        raise ValueError("Organism compilation requires a concrete task.")
    if len(repository_graph_fingerprint) != 64:
        raise ValueError("Organism compilation requires a 64-character repository graph fingerprint.")
    if risk not in {"low", "medium", "high", "critical"} or uncertainty not in {"low", "medium", "high"}:
        raise ValueError("Organism risk or uncertainty classification is invalid.")
    _validate_budget(budget)
    paths = tuple(sorted(set(path.replace("\\", "/").strip("/") for path in target_paths)))
    if any(not path or path.startswith(".") or ".." in path.split("/") for path in paths):
        raise ValueError("Organism target paths must be safe repository-relative paths.")
    needs = tuple(sorted(set(item.strip() for item in evidence_needs if item.strip())))
    if not needs:
        raise ValueError("Organism compilation requires at least one evidence need.")
    roles = _roles_for_archetype(archetype)
    task_digest = canonical_sha256(normalized_task)
    organism_id = f"organism:{canonical_sha256([archetype, task_digest, repository_graph_fingerprint, list(paths)])[:32]}"
    organ_ids = {role: f"{organism_id}:{role}" for role in roles}
    organs: list[OrganSpec] = []
    for index, role in enumerate(roles):
        organ_id = organ_ids[role]
        claims = paths if role == "builder" else ()
        context_identity = canonical_sha256([organism_id, organ_id, role, "isolated-context"])
        organs.append(
            OrganSpec(
                organ_id,
                role,
                _dependencies(role, organ_ids),
                claims,
                _ROLE_INPUTS[role],
                _ROLE_OUTPUTS[role],
                tuple(sorted(set(needs + ("content-digest", "causal-identity", "resource-charge")))),
                _organ_budget(budget, index, len(roles)),
                context_identity,
                _authority(role, shadow_mode),
            )
        )
    analysis = analyze_organ_specs(organs)
    missing_dependencies = analysis["missingDependencies"]
    conflicts = analysis["claimedPathConflicts"]
    distinct_contexts = analysis["distinctContexts"] is True
    acyclic = analysis["acyclic"] is True
    budget_totals = {
        "maximumSeconds": sum(organ.budget.maximum_seconds for organ in organs),
        "maximumProviderCalls": sum(organ.budget.maximum_provider_calls for organ in organs),
        "maximumTokens": sum(organ.budget.maximum_tokens for organ in organs),
        "maximumCostUsd": round(sum(organ.budget.maximum_cost_usd for organ in organs), 8),
    }
    admitted = analysis["admissible"] is True
    payload = {
        "schema": ORGANISM_SCHEMA,
        "id": organism_id,
        "archetype": archetype,
        "task": normalized_task,
        "taskDigest": task_digest,
        "repositoryGraphFingerprint": repository_graph_fingerprint,
        "risk": risk,
        "uncertainty": uncertainty,
        "evidenceNeeds": list(needs),
        "organs": [_organ_to_dict(organ) for organ in organs],
        "admission": {
            "status": "ADMITTED_SHADOW" if admitted and shadow_mode else "ADMITTED" if admitted else "REJECTED",
            "acyclic": acyclic,
            "missingDependencies": missing_dependencies,
            "claimedPathConflicts": conflicts,
            "distinctContexts": distinct_contexts,
            "allOrgansBounded": all(organ.budget.maximum_seconds > 0 for organ in organs),
        },
        "budget": {
            "maximumSeconds": budget.maximum_seconds,
            "maximumProviderCalls": budget.maximum_provider_calls,
            "maximumTokens": budget.maximum_tokens,
            "maximumCostUsd": budget.maximum_cost_usd,
            "allocated": budget_totals,
        },
        "independence": {
            "distinctContextIdentities": distinct_contexts,
            "providerModel": provider_model,
            "providerModelIndependence": "UNAVAILABLE" if provider_model else "UNMEASURED",
            "independenceClaimed": False,
        },
        "execution": {
            "mode": "SHADOW" if shadow_mode else "MISSION_WORKTREE_ONLY",
            "existingMissionWorktreeRequired": True,
            "newLauncherAuthority": False,
            "latentChannelsEnabled": False,
        },
    }
    return payload | {"organismSha256": canonical_sha256(payload)}


def build_evidence_claim(
    organ_id: str,
    kind: str,
    content_digest: str,
    artifact_digest: str | None,
    confidence: float,
    observed_at: datetime,
    eligible_for_knowledge: bool,
) -> EvidenceClaim:
    if not organ_id or not kind or len(content_digest) != 64:
        raise ValueError("Evidence claims require organ, kind, and 64-character content digest.")
    if artifact_digest is not None and len(artifact_digest) != 64:
        raise ValueError("Evidence artifact digest must be 64 characters when supplied.")
    if not 0 <= confidence <= 1:
        raise ValueError("Evidence confidence must be from zero through one.")
    observed = format_utc_timestamp(observed_at, "evidenceClaim.observedAt")
    claim_id = canonical_sha256([organ_id, kind, content_digest, artifact_digest, confidence, observed])
    return EvidenceClaim(claim_id, organ_id, kind, content_digest, artifact_digest, confidence, observed_at, eligible_for_knowledge)


def shared_evidence_space(organism: Mapping[str, object], claims: Sequence[EvidenceClaim]) -> dict[str, object]:
    organs = organism.get("organs")
    if not isinstance(organs, list):
        raise ValueError("Shared evidence space requires an OrganismSpec.")
    organ_ids = {str(item.get("id")) for item in organs if isinstance(item, dict)}
    unknown = sorted({claim.organ_id for claim in claims}.difference(organ_ids))
    if unknown:
        raise ValueError(f"Evidence claims reference unknown organs: {unknown}")
    serialized = [
        {
            "claimId": claim.claim_id,
            "organId": claim.organ_id,
            "kind": claim.kind,
            "contentDigest": claim.content_digest,
            "artifactDigest": claim.artifact_digest,
            "confidence": claim.confidence,
            "observedAt": format_utc_timestamp(claim.observed_at, "evidenceClaim.observedAt"),
            "eligibleForKnowledge": claim.eligible_for_knowledge,
        }
        for claim in sorted(claims, key=lambda item: item.claim_id)
    ]
    payload = {
        "schema": "obtuse.blunder.shared-evidence-space.v1",
        "organismSha256": organism.get("organismSha256"),
        "claims": serialized,
        "hiddenPromptStateAccepted": False,
        "contentAddressedOnly": True,
        "authorityTransferred": False,
    }
    return payload | {"evidenceSpaceSha256": canonical_sha256(payload)}


def lifecycle_event(
    organism: Mapping[str, object],
    state: str,
    sequence: int,
    observed_at: datetime,
    evidence_digests: Sequence[str],
) -> dict[str, object]:
    if state not in LIFECYCLE_STATES or sequence < 1:
        raise ValueError("Organism lifecycle state or sequence is invalid.")
    digests = tuple(sorted(set(evidence_digests)))
    if any(len(item) != 64 for item in digests):
        raise ValueError("Lifecycle evidence digests must be 64 characters.")
    dissolved = LIFECYCLE_STATES.index(state) >= LIFECYCLE_STATES.index("DISSOLVED")
    payload = {
        "schema": "obtuse.blunder.organism-lifecycle-event.v1",
        "organismId": organism.get("id"),
        "organismSha256": organism.get("organismSha256"),
        "state": state,
        "sequence": sequence,
        "observedAt": format_utc_timestamp(observed_at, "organismLifecycle.observedAt"),
        "evidenceDigests": list(digests),
        "runtimeAuthorityActive": not dissolved and state in {"BORN", "ACTIVE", "CHALLENGED", "VALIDATED"},
        "eligibleEvidencePreserved": dissolved,
        "sourceDeleted": False,
    }
    return payload | {"eventSha256": canonical_sha256(payload)}


def shadow_comparison(
    static_budget: Mapping[str, object],
    organism: Mapping[str, object],
    measurements: Sequence[Mapping[str, object]],
    minimum_samples: int,
) -> dict[str, object]:
    organism_budget = organism.get("budget")
    if not isinstance(organism_budget, dict):
        raise ValueError("Shadow comparison requires an OrganismSpec budget.")
    budget_equal = all(static_budget.get(key) == organism_budget.get(key) for key in (
        "maximumSeconds",
        "maximumProviderCalls",
        "maximumTokens",
        "maximumCostUsd",
    ))
    valid = [item for item in measurements if isinstance(item.get("staticSuccess"), bool) and isinstance(item.get("organismSuccess"), bool)]
    safety_regressions = sum(int(item.get("safetyRegressions") or 0) for item in valid)
    static_successes = sum(1 for item in valid if item["staticSuccess"] is True)
    organism_successes = sum(1 for item in valid if item["organismSuccess"] is True)
    enough = len(valid) >= minimum_samples
    match_or_better = enough and organism_successes >= static_successes and safety_regressions == 0 and budget_equal
    status = "MATCH_OR_BETTER" if match_or_better else "INSUFFICIENT_CONFIDENCE" if not enough else "NO_PROMOTION"
    return {
        "schema": "obtuse.blunder.morphogenesis-shadow-comparison.v1",
        "status": status,
        "sampleCount": len(valid),
        "minimumSamples": minimum_samples,
        "equalBudgets": budget_equal,
        "staticSuccesses": static_successes,
        "organismSuccesses": organism_successes,
        "safetyRegressions": safety_regressions,
        "improvementClaimed": status == "MATCH_OR_BETTER" and organism_successes > static_successes,
        "promotionAuthorized": False,
    }
