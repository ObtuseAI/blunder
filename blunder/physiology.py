"""Pure physiological regulation contracts for governed Blunder work."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal, Mapping, Sequence

from blunder.constitutional_kernel import canonical_sha256, format_utc_timestamp
from blunder.resource_accounting import RESOURCE_CHARGE_SCHEMA


PHYSIOLOGY_SCHEMA = "obtuse.blunder.physiology.v1"
HealthStatus = Literal["HEALTHY", "DEGRADED", "OVERLOADED", "STALE", "UNKNOWN", "RECOVERY_BOUND"]


@dataclass(frozen=True)
class HealthThresholds:
    maximum_cpu_time_nanoseconds: int
    maximum_peak_memory_bytes: int
    maximum_queue_ratio: float
    maximum_disk_write_bytes: int
    warning_ratio: float
    recovery_ratio: float


@dataclass(frozen=True)
class Opportunity:
    opportunity_id: str
    kind: str
    priority: int
    estimated_seconds: int
    evidence_digest: str
    rationale: str


@dataclass(frozen=True)
class RoutingTombstone:
    tombstone_id: str
    strategy_id: str
    created_at: datetime
    expires_at: datetime
    evidence_digests: tuple[str, ...]
    reason_code: str
    appeal_requested: bool


def _metric_value(charge: Mapping[str, object], name: str) -> float | None:
    metrics = charge.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("ResourceCharge metrics must be an object.")
    metric = metrics.get(name)
    if not isinstance(metric, dict):
        raise ValueError(f"ResourceCharge metric is missing: {name}")
    state = metric.get("state")
    if state == "UNMEASURED":
        return None
    value = metric.get("value")
    if state != "MEASURED" or not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ValueError(f"ResourceCharge metric is invalid: {name}")
    return float(value)


def _freshness_state(charge: Mapping[str, object]) -> str:
    chronos = charge.get("chronos")
    if not isinstance(chronos, dict):
        raise ValueError("ResourceCharge Chronos stamp must be an object.")
    freshness = chronos.get("evidenceFreshness")
    if not isinstance(freshness, dict):
        raise ValueError("ResourceCharge evidence freshness must be an object.")
    return str(freshness.get("state") or "")


def derive_health_vector(
    charge: Mapping[str, object],
    thresholds: HealthThresholds,
    previous_status: HealthStatus | None,
) -> dict[str, object]:
    """Derive health from measured evidence; unknown inputs can never become healthy."""

    if charge.get("schema") != RESOURCE_CHARGE_SCHEMA:
        raise ValueError("Health derivation requires a supported ResourceCharge.")
    if not 0 < thresholds.warning_ratio < 1:
        raise ValueError("Health warning ratio must be between zero and one.")
    if not 0 < thresholds.recovery_ratio < thresholds.warning_ratio:
        raise ValueError("Health recovery ratio must be below the warning ratio.")
    if thresholds.maximum_cpu_time_nanoseconds < 1 or thresholds.maximum_peak_memory_bytes < 1:
        raise ValueError("Health CPU and memory thresholds must be positive.")
    if not 0 < thresholds.maximum_queue_ratio <= 1 or thresholds.maximum_disk_write_bytes < 1:
        raise ValueError("Health queue and disk thresholds are invalid.")
    duration = _metric_value(charge, "processDurationNanoseconds")
    cpu_time = _metric_value(charge, "cpuTimeNanoseconds")
    peak_memory = _metric_value(charge, "peakMemoryBytes")
    disk_write = _metric_value(charge, "diskWriteBytes")
    queue_depth = _metric_value(charge, "queueDepth")
    queue_capacity = _metric_value(charge, "queueCapacity")
    values = {
        "processDurationNanoseconds": duration,
        "cpuTimeNanoseconds": cpu_time,
        "peakMemoryBytes": peak_memory,
        "diskWriteBytes": disk_write,
        "queueDepth": queue_depth,
        "queueCapacity": queue_capacity,
    }
    unknown = sorted(name for name, value in values.items() if value is None)
    freshness = _freshness_state(charge)
    ratios: dict[str, float] = {}
    if not unknown:
        assert duration is not None
        assert cpu_time is not None
        assert peak_memory is not None
        assert disk_write is not None
        assert queue_depth is not None
        assert queue_capacity is not None
        if duration <= 0 or queue_capacity <= 0:
            raise ValueError("Measured duration and queue capacity must be positive for health derivation.")
        ratios = {
            "cpuTime": cpu_time / thresholds.maximum_cpu_time_nanoseconds,
            "memory": peak_memory / thresholds.maximum_peak_memory_bytes,
            "queue": (queue_depth / queue_capacity) / thresholds.maximum_queue_ratio,
            "diskWrite": disk_write / thresholds.maximum_disk_write_bytes,
        }
    limiting_ratio = max(ratios.values()) if ratios else None
    if freshness == "STALE":
        status: HealthStatus = "STALE"
        reasons = ["EVIDENCE_STALE"]
    elif freshness not in {"FRESH"}:
        status = "UNKNOWN"
        reasons = ["EVIDENCE_FRESHNESS_UNKNOWN"]
    elif unknown:
        status = "UNKNOWN"
        reasons = [f"TELEMETRY_UNMEASURED:{name}" for name in unknown]
    elif previous_status in {"OVERLOADED", "RECOVERY_BOUND"} and limiting_ratio is not None and limiting_ratio > thresholds.recovery_ratio:
        status = "RECOVERY_BOUND"
        reasons = ["HYSTERESIS_RECOVERY_THRESHOLD_NOT_MET"]
    elif limiting_ratio is not None and limiting_ratio > 1:
        status = "OVERLOADED"
        reasons = [name for name, ratio in ratios.items() if ratio > 1]
    elif limiting_ratio is not None and limiting_ratio >= thresholds.warning_ratio:
        status = "DEGRADED"
        reasons = [name for name, ratio in ratios.items() if ratio >= thresholds.warning_ratio]
    else:
        status = "HEALTHY"
        reasons = ["ALL_MEASURED_DIMENSIONS_WITHIN_THRESHOLD"]
    payload = {
        "schema": PHYSIOLOGY_SCHEMA,
        "status": status,
        "previousStatus": previous_status,
        "resourceChargeSha256": charge.get("chargeSha256"),
        "freshness": freshness,
        "unknownMetrics": unknown,
        "ratios": {name: round(value, 6) for name, value in ratios.items()},
        "limitingRatio": round(limiting_ratio, 6) if limiting_ratio is not None else None,
        "thresholds": asdict(thresholds),
        "reasonCodes": reasons,
        "authority": {"classificationOnly": True, "effectAuthority": False},
    }
    return payload | {"healthSha256": canonical_sha256(payload)}


def homeostatic_plan(
    health_status: HealthStatus,
    current_parallelism: int,
    current_candidate_budget: int,
) -> dict[str, object]:
    if current_parallelism < 1 or current_candidate_budget < 1:
        raise ValueError("Homeostatic planning requires positive current limits.")
    actions_by_status: dict[HealthStatus, tuple[str, ...]] = {
        "HEALTHY": ("NO_OP",),
        "DEGRADED": ("REDUCE_PARALLELISM", "REDUCE_CANDIDATE_BUDGET"),
        "OVERLOADED": (
            "REDUCE_PARALLELISM",
            "REDUCE_CANDIDATE_BUDGET",
            "PAUSE_INFLOW",
            "QUARANTINE_MUTATION",
            "SCHEDULE_RECOVERY_PROOF",
        ),
        "STALE": ("PAUSE_INFLOW", "SCHEDULE_RECOVERY_PROOF", "REQUEST_OPERATOR_REVIEW"),
        "UNKNOWN": ("PAUSE_INFLOW", "SCHEDULE_RECOVERY_PROOF", "REQUEST_OPERATOR_REVIEW"),
        "RECOVERY_BOUND": (
            "REDUCE_PARALLELISM",
            "REDUCE_CANDIDATE_BUDGET",
            "PAUSE_INFLOW",
            "QUARANTINE_MUTATION",
            "SCHEDULE_RECOVERY_PROOF",
        ),
    }
    actions = actions_by_status[health_status]
    payload = {
        "schema": "obtuse.blunder.homeostatic-plan.v1",
        "healthStatus": health_status,
        "actions": list(actions),
        "limits": {
            "parallelism": max(1, current_parallelism - 1) if "REDUCE_PARALLELISM" in actions else current_parallelism,
            "candidateBudget": max(1, current_candidate_budget // 2) if "REDUCE_CANDIDATE_BUDGET" in actions else current_candidate_budget,
        },
        "authority": {
            "advisoryOnly": True,
            "authorityExpansion": False,
            "sourceMutation": False,
            "historyDeletion": False,
        },
    }
    return payload | {"planSha256": canonical_sha256(payload)}


def default_opportunity_queue(graph: Mapping[str, object]) -> tuple[Opportunity, ...]:
    counts = graph.get("counts") if isinstance(graph.get("counts"), dict) else {}
    changed_paths = graph.get("changedPaths") if isinstance(graph.get("changedPaths"), list) else []
    definitions = (
        ("semantic-hotspot-map", "semantic-analysis", 100, 20, f"Rank dependency fan-in across {counts.get('nodes', 0)} semantic nodes."),
        ("changed-impact-refresh", "impact-analysis", 95 if changed_paths else 45, 20, f"Recompute impact for {len(changed_paths)} changed source paths."),
        ("deterministic-proof-refresh", "proof-refresh", 90, 90, "Refresh allowlisted proof in isolated validation shards."),
        ("contradiction-mining", "contradiction-search", 80, 45, "Compare source, documentation, and proof digests."),
        ("recovery-drill-plan", "recovery-planning", 70, 30, "Plan one bounded non-destructive recovery drill."),
    )
    graph_digest = str(graph.get("fingerprint") or "")
    return tuple(
        Opportunity(identifier, kind, priority, seconds, graph_digest, rationale)
        for identifier, kind, priority, seconds, rationale in definitions
    )


def select_opportunities(
    opportunities: Sequence[Opportunity],
    maximum_tasks: int,
    maximum_seconds: int,
    health_status: HealthStatus,
    tombstoned_strategy_ids: Sequence[str],
) -> dict[str, object]:
    if maximum_tasks < 1 or maximum_seconds < 1:
        raise ValueError("Opportunity selection budgets must be positive.")
    if len({item.opportunity_id for item in opportunities}) != len(opportunities):
        raise ValueError("Opportunity ids must be unique.")
    if any(
        not item.opportunity_id
        or not item.kind
        or not 0 <= item.priority <= 100
        or item.estimated_seconds < 1
        or len(item.evidence_digest) != 64
        or not item.rationale
        for item in opportunities
    ):
        raise ValueError("Opportunity contracts require bounded priority, duration, evidence, and rationale.")
    blocked = frozenset(tombstoned_strategy_ids)
    selected: list[Opportunity] = []
    elapsed = 0
    if health_status == "HEALTHY":
        for opportunity in sorted(opportunities, key=lambda item: (-item.priority, item.opportunity_id)):
            if opportunity.opportunity_id in blocked:
                continue
            if len(selected) >= maximum_tasks or elapsed + opportunity.estimated_seconds > maximum_seconds:
                continue
            selected.append(opportunity)
            elapsed += opportunity.estimated_seconds
    payload = {
        "schema": "obtuse.blunder.opportunity-selection.v1",
        "healthStatus": health_status,
        "candidateCount": len(opportunities),
        "selected": [
            {
                "id": item.opportunity_id,
                "kind": item.kind,
                "priority": item.priority,
                "seconds": item.estimated_seconds,
                "evidenceDigest": item.evidence_digest,
                "reason": item.rationale,
                "sourceMutation": False,
            }
            for item in selected
        ],
        "plannedSeconds": elapsed,
        "tombstonedStrategyIds": sorted(blocked),
        "authority": {"queueMutation": False, "sourceMutation": False, "providerCalls": 0},
    }
    return payload | {"selectionSha256": canonical_sha256(payload)}


def build_routing_tombstone(
    strategy_id: str,
    created_at: datetime,
    expires_at: datetime,
    evidence_digests: Sequence[str],
    reason_code: str,
    appeal_requested: bool,
) -> RoutingTombstone:
    if created_at.tzinfo is None or expires_at.tzinfo is None:
        raise ValueError("Routing tombstone times must include an explicit timezone.")
    created = created_at.astimezone(timezone.utc)
    expires = expires_at.astimezone(timezone.utc)
    if expires <= created:
        raise ValueError("Routing tombstone expiry must follow creation.")
    digests = tuple(sorted(set(evidence_digests)))
    if not strategy_id or not reason_code or not digests:
        raise ValueError("Routing tombstones require strategy, reason, and evidence.")
    if any(len(digest) != 64 for digest in digests):
        raise ValueError("Routing tombstone evidence digests must be 64 characters.")
    identity = canonical_sha256([strategy_id, format_utc_timestamp(created, "createdAt"), list(digests), reason_code])
    return RoutingTombstone(identity, strategy_id, created, expires, digests, reason_code, appeal_requested)


def tombstone_status(tombstone: RoutingTombstone, observed_at: datetime) -> dict[str, object]:
    if observed_at.tzinfo is None:
        raise ValueError("Routing tombstone observation time must include an explicit timezone.")
    observed = observed_at.astimezone(timezone.utc)
    status = "APPEALED" if tombstone.appeal_requested else "EXPIRED" if observed >= tombstone.expires_at else "ACTIVE"
    return {
        "schema": "obtuse.blunder.routing-tombstone-state.v1",
        "status": status,
        "tombstone": {
            **asdict(tombstone),
            "created_at": format_utc_timestamp(tombstone.created_at, "createdAt"),
            "expires_at": format_utc_timestamp(tombstone.expires_at, "expiresAt"),
            "evidence_digests": list(tombstone.evidence_digests),
        },
        "routingBlocked": status == "ACTIVE",
        "sourceDeleted": False,
        "historyDeleted": False,
        "replayable": True,
    }


def cognitive_physics(
    probabilities: Sequence[float],
    previous_utility: float,
    current_utility: float,
    elapsed_seconds: float,
    unresolved_items: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    if not probabilities or any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or value < 0
        for value in probabilities
    ):
        raise ValueError("Entropy probabilities must be a non-empty non-negative sequence.")
    if not all(math.isfinite(value) for value in (previous_utility, current_utility, elapsed_seconds)):
        raise ValueError("Cognitive utility and elapsed time must be finite.")
    total = float(sum(probabilities))
    if total <= 0 or elapsed_seconds <= 0:
        raise ValueError("Entropy probability mass and momentum elapsed time must be positive.")
    normalized = tuple(float(value) / total for value in probabilities)
    entropy_bits = -sum(value * math.log2(value) for value in normalized if value > 0)
    maximum_entropy = math.log2(len(normalized)) if len(normalized) > 1 else 0.0
    potential = 0.0
    for index, item in enumerate(unresolved_items):
        impact = item.get("impact")
        uncertainty = item.get("uncertainty")
        estimated_cost = item.get("estimatedCost")
        if not isinstance(impact, (int, float)) or isinstance(impact, bool) or impact < 0:
            raise ValueError(f"Unresolved item {index} impact is invalid.")
        if not isinstance(uncertainty, (int, float)) or isinstance(uncertainty, bool) or not 0 <= uncertainty <= 1:
            raise ValueError(f"Unresolved item {index} uncertainty is invalid.")
        if not isinstance(estimated_cost, (int, float)) or isinstance(estimated_cost, bool) or estimated_cost <= 0:
            raise ValueError(f"Unresolved item {index} estimatedCost is invalid.")
        potential += float(impact) * float(uncertainty) / float(estimated_cost)
    return {
        "schema": "obtuse.blunder.cognitive-physics.v1",
        "entropyBits": round(entropy_bits, 9),
        "normalizedEntropy": round(entropy_bits / maximum_entropy, 9) if maximum_entropy > 0 else 0.0,
        "momentumUtilityPerSecond": round((current_utility - previous_utility) / elapsed_seconds, 9),
        "potential": round(potential, 9),
        "definitions": {
            "entropy": "Shannon entropy of normalized competing-belief mass in bits.",
            "momentum": "Change in predeclared utility divided by measured elapsed seconds.",
            "potential": "Sum of unresolved impact times uncertainty divided by estimated resolution cost.",
        },
        "authority": {"measurementOnly": True, "selectionAuthority": False},
    }
