"""Provider-free end-to-end canaries for the durable Blunder CCOS runtime."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta

from blunder.ccos_runtime import CCOSRuntimeConfig, append_runtime_record, require_runtime_capacity, runtime_state
from blunder.cognitive_ecology import (
    CAPSULE_SCHEMA,
    CapsuleChecks,
    build_memory_record,
    build_mutation_genome,
    canonical_digest,
    memory_transition,
    verify_and_quarantine_capsule,
)
from blunder.evolution.sealed_lab import (
    EvaluationControls,
    MutationOperation,
    RealityObservation,
    build_candidate_lineage,
    build_mutation_ir,
    evaluate_sealed_candidate,
)
from blunder.sovereign_observatory import build_event, event_payload


ACTIVATION_CANARY_CONFIRMATION = "SIMULATE_CCOS_ACTIVATION_GATE"
ROLLBACK_CANARY_CONFIRMATION = "VERIFY_CCOS_ROLLBACK_GATE"


def _digest(value: str) -> str:
    return canonical_digest({"ccosCanary": value})


def _reject_signature(_payload: bytes, _signature: str, _signer: str) -> bool:
    return False


def _held_out_observations() -> tuple[RealityObservation, ...]:
    reality_ids = (
        "conservative-baseline",
        "aggressive-bounded",
        "adversarial",
        "historical-replay",
        "degraded-dependency",
        "fresh-heldout",
        "synthetic-future",
    )
    return tuple(
        RealityObservation(
            reality_id=reality_id,
            success=0.4 if reality_id == "fresh-heldout" else 0.92,
            safety=1.0,
            reproducibility=1.0,
            rollback=1.0,
            novelty=0.5,
            efficiency=0.8,
            confidence=0.45 if reality_id == "fresh-heldout" else 0.9,
            evidence_digest=_digest(f"reality:{reality_id}"),
            contaminated=reality_id == "fresh-heldout",
            deterministic=True,
        )
        for reality_id in reality_ids
    )


def _authority_is_closed(config: CCOSRuntimeConfig) -> bool:
    return not any(
        (
            config.provider_calls_allowed,
            config.automatic_activation_allowed,
            config.federation_allowed,
            config.meta_evolution_allowed,
        )
    )


def run_operational_canary(
    config: CCOSRuntimeConfig,
    observed_at: datetime,
    activation_confirmation: str,
    rollback_confirmation: str,
) -> dict[str, object]:
    """Persist all core evidence types and prove both effect gates remain closed."""

    if observed_at.tzinfo is None:
        raise ValueError("CCOS operational canary time must be timezone-aware.")
    if activation_confirmation != ACTIVATION_CANARY_CONFIRMATION:
        raise PermissionError(f"CCOS activation canary requires exact confirmation: {ACTIVATION_CANARY_CONFIRMATION}")
    if rollback_confirmation != ROLLBACK_CANARY_CONFIRMATION:
        raise PermissionError(f"CCOS rollback canary requires exact confirmation: {ROLLBACK_CANARY_CONFIRMATION}")
    if not _authority_is_closed(config):
        raise PermissionError("CCOS operational canary refuses to run while any effect authority is open.")

    require_runtime_capacity(config, 7)
    initial = runtime_state(config)
    genome = build_mutation_genome(
        prompts=("inspect evidence before proposing a bounded change",),
        context_rules=("preserve blocked and no-champion states",),
        organ_topology=(("governor", "builder"), ("builder", "validator")),
        tools=("read-source", "run-validation"),
        non_root_policy=("no-activation", "no-provider", "no-credential-access"),
        evaluators=("historical-replay", "fresh-heldout", "adversarial"),
        compatibility=("ccos-runtime-v1",),
        lineage=(_digest("genome-parent"),),
        nondeterminism_bound=0.0,
    )
    mutation = build_mutation_ir(
        (
            MutationOperation(
                layer="routing",
                target="candidate.route.evidence-order",
                operator="replace",
                value="evidence-first",
            ),
        )
    )
    lineage = build_candidate_lineage(
        mutation=mutation,
        parent_digests=(genome.genome_digest,),
        operator_id="ccos-provider-free-canary",
        rationale="Exercise durable lineage without integrating a candidate.",
        expected_effect="No live effect; held-out evaluation must preserve no champion.",
        rollback_digest=_digest("exact-pre-canary-runtime"),
        compatibility=("ccos-runtime-v1",),
    )
    evaluation = evaluate_sealed_candidate(
        lineage=lineage,
        observations=_held_out_observations(),
        controls=EvaluationControls(
            static_court_passed=True,
            isolated_worktree=True,
            deterministic_validation_passed=True,
            quorum_passed=True,
            rollback_drill_passed=True,
            champion_floor=0.85,
            live_tree_before=_digest("unchanged-live-tree"),
            live_tree_after=_digest("unchanged-live-tree"),
        ),
    )
    if evaluation["promotionStatus"] != "NO_CHAMPION" or evaluation["activationAuthorized"] is not False:
        raise RuntimeError("CCOS held-out canary failed to preserve the required no-champion state.")
    memory = build_memory_record(
        tier="episode",
        claim_digest=_digest("canary-observation"),
        evidence_digests=(_digest("canary-evidence"),),
        independent_sources=1,
        domains=("runtime-canary",),
        contradictor_searches=1,
        contradictions=(),
        confidence=0.5,
        uncertainty=0.5,
        created_at=observed_at,
        refreshed_at=observed_at,
        expires_at=observed_at + timedelta(days=1),
        supersedes=(),
        operator_approved=False,
    )
    transition = memory_transition(memory, "mission", observed_at)
    capsule = {
        "schema": CAPSULE_SCHEMA,
        "genomeDigest": genome.genome_digest,
        "authorityTransferred": False,
        "signatureAlgorithm": "CANARY-INVALID",
        "signerIdentity": "untrusted-canary-peer",
        "signature": "INVALID",
    }
    admission = verify_and_quarantine_capsule(
        capsule,
        CapsuleChecks(True, True, True, True, True),
        _reject_signature,
    )
    if admission["status"] != "REJECTED" or admission["quarantined"] is not True:
        raise RuntimeError("CCOS quarantine canary failed closed admission.")
    observatory_event = build_event(
        sequence=0,
        previous_digest=None,
        recorded_at=observed_at,
        monotonic_ns=0,
        trace_id=_digest("trace").lower()[:32],
        span_id=_digest("span").lower()[:16],
        parent_span_id=None,
        kind="simulation",
        retention_class="audit",
        redaction_class="internal",
        resource={"service.name": "blunder-ccos-canary"},
        attributes={"value": "provider-free", "promotionStatus": "NO_CHAMPION"},
        evidence_digest=str(evaluation["evaluationIdentity"]),
        effectful=False,
        policy_event_digest=None,
    )

    persisted = (
        append_runtime_record(config, "genome", asdict(genome), observed_at),
        append_runtime_record(config, "lineage", asdict(lineage), observed_at + timedelta(microseconds=1)),
        append_runtime_record(config, "evaluation", evaluation, observed_at + timedelta(microseconds=2)),
        append_runtime_record(
            config,
            "memory",
            {
                "memoryId": memory.memory_id,
                "tier": memory.tier,
                "claimDigest": memory.claim_digest,
                "evidenceDigests": list(memory.evidence_digests),
                "transition": transition,
            },
            observed_at + timedelta(microseconds=3),
        ),
        append_runtime_record(
            config,
            "observatory",
            event_payload(observatory_event),
            observed_at + timedelta(microseconds=4),
        ),
        append_runtime_record(config, "quarantine", admission, observed_at + timedelta(microseconds=5)),
    )
    final = runtime_state(config)
    expected_generation = int(initial["generation"]) + len(persisted)
    exact_persistence = final["generation"] == expected_generation and final["headDigest"] == persisted[-1]["recordDigest"]
    if not exact_persistence:
        raise RuntimeError("CCOS operational canary persistence did not reload exactly.")
    report = {
        "schema": "obtuse.blunder.ccos-operational-canary.v1",
        "status": "PASS_AUTHORITY_CLOSED_NO_CHAMPION",
        "initialIndexDigest": initial["indexDigest"],
        "evidencePlaneIndexDigest": final["indexDigest"],
        "persistedRecordDigests": [record["recordDigest"] for record in persisted],
        "exactPersistence": True,
        "heldOutEvaluation": evaluation["promotionStatus"],
        "quarantineAdmission": admission["status"],
        "activationCanary": "BLOCKED_BY_CONFIGURATION",
        "rollbackCanary": "VERIFIED_NO_EFFECT_TO_ROLL_BACK",
        "providerCalls": 0,
        "authorityGranted": False,
        "automaticActivation": False,
        "liveTreeChanged": False,
    }
    canary_record = append_runtime_record(config, "canary", report, observed_at + timedelta(microseconds=6))
    closed_state = runtime_state(config)
    if closed_state["generation"] != expected_generation + 1 or closed_state["headDigest"] != canary_record["recordDigest"]:
        raise RuntimeError("CCOS operational canary report did not reload exactly.")
    return report | {
        "canaryRecordDigest": canary_record["recordDigest"],
        "runtimeIndexDigest": closed_state["indexDigest"],
        "runtimeGeneration": closed_state["generation"],
    }
