"""Evidence-first append-only observatory for governed Blunder cognition."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Mapping, Sequence

from blunder.cognitive_ecology import canonical_digest, require_digest


EVENT_SCHEMA = "obtuse.blunder.observatory-event.v1"
PROJECTION_SCHEMA = "obtuse.blunder.observatory-projection.v1"
AUDIT_SCHEMA = "obtuse.blunder.observatory-audit.v1"
TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")

RetentionClass = Literal["ephemeral", "operational", "audit", "constitutional"]
RedactionClass = Literal["public", "internal", "sensitive", "secret-prohibited"]

EVENT_KINDS: tuple[str, ...] = (
    "policy-decision",
    "resource-charge",
    "evidence-claim",
    "organism-birth",
    "organism-death",
    "handoff",
    "challenge",
    "mutation",
    "simulation",
    "fitness",
    "memory-promotion",
    "federation",
    "rollback",
    "operator-action",
)

PROJECTION_KINDS: tuple[str, ...] = (
    "information-flow",
    "energy",
    "entropy",
    "health",
    "uncertainty",
    "authority",
    "mutation-lineage",
    "genome-niches",
    "knowledge-diffusion",
    "pressure",
)

PROHIBITED_COGNITION_FIELDS: frozenset[str] = frozenset(
    {"chainOfThought", "chain_of_thought", "rawReasoning", "reasoningTokens", "hiddenPromptState"}
)
SENSITIVE_MARKERS: tuple[str, ...] = ("secret", "token", "password", "credential", "authorization", "cookie")


@dataclass(frozen=True)
class ObservatoryEvent:
    schema: str
    sequence: int
    previous_digest: str | None
    recorded_at: str
    monotonic_ns: int
    trace_id: str
    span_id: str
    parent_span_id: str | None
    otel_name: str
    kind: str
    retention_class: RetentionClass
    redaction_class: RedactionClass
    resource: Mapping[str, str]
    attributes: Mapping[str, object]
    evidence_digest: str
    effectful: bool
    policy_event_digest: str | None
    redaction_count: int
    event_digest: str


def _validate_trace_id(value: str) -> str:
    if TRACE_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("Trace id must be 32 lowercase hexadecimal characters.")
    return value


def _validate_span_id(value: str, context: str) -> str:
    if SPAN_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{context} must be 16 lowercase hexadecimal characters.")
    return value


def _redact(value: object, key: str) -> tuple[object, int]:
    normalized = key.casefold()
    if any(marker in normalized for marker in SENSITIVE_MARKERS):
        return "[REDACTED]", 1
    if isinstance(value, Mapping):
        redacted: dict[str, object] = {}
        count = 0
        for nested_key, nested_value in value.items():
            item, observed = _redact(nested_value, str(nested_key))
            redacted[str(nested_key)] = item
            count += observed
        return redacted, count
    if isinstance(value, (list, tuple)):
        redacted_items: list[object] = []
        count = 0
        for item in value:
            redacted_item, observed = _redact(item, key)
            redacted_items.append(redacted_item)
            count += observed
        return redacted_items, count
    return value, 0


def build_event(
    sequence: int,
    previous_digest: str | None,
    recorded_at: datetime,
    monotonic_ns: int,
    trace_id: str,
    span_id: str,
    parent_span_id: str | None,
    kind: str,
    retention_class: RetentionClass,
    redaction_class: RedactionClass,
    resource: Mapping[str, str],
    attributes: Mapping[str, object],
    evidence_digest: str,
    effectful: bool,
    policy_event_digest: str | None,
) -> ObservatoryEvent:
    if sequence < 0 or monotonic_ns < 0:
        raise ValueError("Event sequence and monotonic time must be non-negative.")
    expected_previous = None if sequence == 0 else require_digest(str(previous_digest or ""), "Previous event")
    if sequence == 0 and previous_digest is not None:
        raise ValueError("Genesis event must not have a previous digest.")
    if recorded_at.tzinfo is None:
        raise ValueError("Event wall time must be timezone-aware.")
    if kind not in EVENT_KINDS:
        raise ValueError(f"Unsupported observatory event kind: {kind}")
    if retention_class not in {"ephemeral", "operational", "audit", "constitutional"}:
        raise ValueError(f"Unsupported retention class: {retention_class}")
    if redaction_class not in {"public", "internal", "sensitive", "secret-prohibited"}:
        raise ValueError(f"Unsupported redaction class: {redaction_class}")
    prohibited = PROHIBITED_COGNITION_FIELDS.intersection(attributes)
    if prohibited:
        raise PermissionError(f"Raw cognition fields are prohibited: {sorted(prohibited)}")
    if effectful and policy_event_digest is None:
        raise ValueError("Effectful event requires a policy event digest.")
    policy_digest = require_digest(policy_event_digest, "Policy event") if policy_event_digest is not None else None
    redacted_attributes, redaction_count = _redact(attributes, "attributes")
    redacted_resource, resource_redactions = _redact(resource, "resource")
    if not isinstance(redacted_attributes, dict) or not isinstance(redacted_resource, dict):
        raise TypeError("Redacted observatory payload is invalid.")
    core = {
        "schema": EVENT_SCHEMA,
        "sequence": sequence,
        "previousDigest": expected_previous,
        "recordedAt": recorded_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "monotonicNs": monotonic_ns,
        "traceId": _validate_trace_id(trace_id),
        "spanId": _validate_span_id(span_id, "Span id"),
        "parentSpanId": _validate_span_id(parent_span_id, "Parent span id") if parent_span_id is not None else None,
        "otelName": f"blunder.{kind.replace('-', '.')}",
        "kind": kind,
        "retentionClass": retention_class,
        "redactionClass": redaction_class,
        "resource": redacted_resource,
        "attributes": redacted_attributes,
        "evidenceDigest": require_digest(evidence_digest, "Event evidence"),
        "effectful": effectful,
        "policyEventDigest": policy_digest,
        "redactionCount": redaction_count + resource_redactions,
    }
    return ObservatoryEvent(
        schema=EVENT_SCHEMA,
        sequence=sequence,
        previous_digest=expected_previous,
        recorded_at=core["recordedAt"],
        monotonic_ns=monotonic_ns,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        otel_name=core["otelName"],
        kind=kind,
        retention_class=retention_class,
        redaction_class=redaction_class,
        resource=redacted_resource,
        attributes=redacted_attributes,
        evidence_digest=core["evidenceDigest"],
        effectful=effectful,
        policy_event_digest=policy_digest,
        redaction_count=core["redactionCount"],
        event_digest=canonical_digest(core),
    )


def event_payload(event: ObservatoryEvent) -> dict[str, object]:
    value = asdict(event)
    return {
        "schema": value["schema"],
        "sequence": value["sequence"],
        "previousDigest": value["previous_digest"],
        "recordedAt": value["recorded_at"],
        "monotonicNs": value["monotonic_ns"],
        "traceId": value["trace_id"],
        "spanId": value["span_id"],
        "parentSpanId": value["parent_span_id"],
        "otelName": value["otel_name"],
        "kind": value["kind"],
        "retentionClass": value["retention_class"],
        "redactionClass": value["redaction_class"],
        "resource": value["resource"],
        "attributes": value["attributes"],
        "evidenceDigest": value["evidence_digest"],
        "effectful": value["effectful"],
        "policyEventDigest": value["policy_event_digest"],
        "redactionCount": value["redaction_count"],
        "eventDigest": value["event_digest"],
    }


def append_event(path: Path, event: ObservatoryEvent) -> None:
    existing = load_events(path) if path.exists() else ()
    if event.sequence != len(existing):
        raise ValueError(f"Append sequence mismatch: expected {len(existing)}, observed {event.sequence}.")
    expected_previous = existing[-1].event_digest if existing else None
    if event.previous_digest != expected_previous:
        raise ValueError("Append event previous digest does not match ledger head.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event_payload(event), sort_keys=True, separators=(",", ":")) + "\n")


def parse_event(value: Mapping[str, object]) -> ObservatoryEvent:
    claimed_digest = require_digest(str(value.get("eventDigest") or ""), "Event")
    core = {key: item for key, item in value.items() if key != "eventDigest"}
    if canonical_digest(core) != claimed_digest:
        raise ValueError("Observatory event digest mismatch.")
    return ObservatoryEvent(
        schema=str(value["schema"]),
        sequence=int(value["sequence"]),
        previous_digest=str(value["previousDigest"]) if value.get("previousDigest") is not None else None,
        recorded_at=str(value["recordedAt"]),
        monotonic_ns=int(value["monotonicNs"]),
        trace_id=str(value["traceId"]),
        span_id=str(value["spanId"]),
        parent_span_id=str(value["parentSpanId"]) if value.get("parentSpanId") is not None else None,
        otel_name=str(value["otelName"]),
        kind=str(value["kind"]),
        retention_class=str(value["retentionClass"]),  # type: ignore[arg-type]
        redaction_class=str(value["redactionClass"]),  # type: ignore[arg-type]
        resource=dict(value["resource"]),  # type: ignore[arg-type]
        attributes=dict(value["attributes"]),  # type: ignore[arg-type]
        evidence_digest=str(value["evidenceDigest"]),
        effectful=value["effectful"] is True,
        policy_event_digest=str(value["policyEventDigest"]) if value.get("policyEventDigest") is not None else None,
        redaction_count=int(value["redactionCount"]),
        event_digest=claimed_digest,
    )


def load_events(path: Path) -> tuple[ObservatoryEvent, ...]:
    events: list[ObservatoryEvent] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid observatory JSON at line {line_number}: {error}") from error
        if not isinstance(value, dict):
            raise TypeError(f"Observatory event at line {line_number} must be an object.")
        event = parse_event(value)
        expected_previous = events[-1].event_digest if events else None
        if event.sequence != len(events) or event.previous_digest != expected_previous:
            raise ValueError(f"Observatory hash chain is invalid at line {line_number}.")
        events.append(event)
    return tuple(events)


def build_projection(events: Sequence[ObservatoryEvent], projection_kind: str) -> dict[str, object]:
    if projection_kind not in PROJECTION_KINDS:
        raise ValueError(f"Unsupported observatory projection: {projection_kind}")
    claims = [
        {
            "claim": f"{projection_kind}:{event.kind}:{event.sequence}",
            "sourceEventDigest": event.event_digest,
            "evidenceDigest": event.evidence_digest,
            "traceId": event.trace_id,
            "spanId": event.span_id,
            "value": event.attributes.get(projection_kind, event.attributes.get("value")),
        }
        for event in events
    ]
    return {
        "schema": PROJECTION_SCHEMA,
        "projectionKind": projection_kind,
        "sourceLedgerHead": events[-1].event_digest if events else None,
        "claims": claims,
        "claimCount": len(claims),
        "allClaimsEvidenceLinked": all(item["sourceEventDigest"] and item["evidenceDigest"] for item in claims),
        "sourceOfTruth": False,
        "deletionAffectsEvidence": False,
    }


def audit_observatory(events: Sequence[ObservatoryEvent], expected_non_effect_span_ids: Sequence[str]) -> dict[str, object]:
    observed_spans = {event.span_id for event in events}
    expected = set(expected_non_effect_span_ids)
    present = expected.intersection(observed_spans)
    missing = sorted(expected.difference(observed_spans))
    completeness = len(present) / len(expected) if expected else 1.0
    effectful = [event for event in events if event.effectful]
    effect_policy_coverage = sum(1 for event in effectful if event.policy_event_digest is not None) / len(effectful) if effectful else 1.0
    orphan_spans = sorted(
        event.span_id
        for event in events
        if event.parent_span_id is not None and event.parent_span_id not in observed_spans
    )
    clock_skew = [
        events[index].span_id
        for index in range(1, len(events))
        if events[index].monotonic_ns < events[index - 1].monotonic_ns
        or datetime.fromisoformat(events[index].recorded_at.replace("Z", "+00:00"))
        < datetime.fromisoformat(events[index - 1].recorded_at.replace("Z", "+00:00"))
    ]
    missing_telemetry = sorted(
        event.span_id for event in events if event.attributes.get("telemetryPresent") is not True
    )
    status = (
        "PASS"
        if effect_policy_coverage == 1.0 and completeness >= 0.995 and not orphan_spans and not clock_skew and not missing_telemetry
        else "FAIL"
    )
    return {
        "schema": AUDIT_SCHEMA,
        "status": status,
        "effectfulActionCount": len(effectful),
        "effectPolicyCausalCoverage": round(effect_policy_coverage, 6),
        "expectedNonEffectSpanCount": len(expected),
        "observedExpectedNonEffectSpanCount": len(present),
        "nonEffectTraceCompleteness": round(completeness, 6),
        "missingSpanIds": missing,
        "orphanSpanIds": orphan_spans,
        "redactedValueCount": sum(event.redaction_count for event in events),
        "missingTelemetrySpanIds": missing_telemetry,
        "clockSkewSpanIds": clock_skew,
        "rawChainOfThoughtCaptured": False,
    }


def dunce_consumer_view(projection: Mapping[str, object]) -> dict[str, object]:
    if projection.get("schema") != PROJECTION_SCHEMA:
        raise ValueError("Dunce requires a validated observatory projection.")
    return {
        "status": "READ_ONLY_ANALYSIS_VIEW",
        "projectionKind": projection.get("projectionKind"),
        "claimCount": projection.get("claimCount"),
        "policyMutationAuthorized": False,
        "truthMutationAuthorized": False,
        "fitnessMutationAuthorized": False,
        "promotionMutationAuthorized": False,
    }
