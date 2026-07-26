"""Pure constitutional policy contracts for Blunder shadow evaluation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Mapping, Sequence, cast


DecisionStatus = Literal["ALLOW", "DENY"]
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


@dataclass(frozen=True)
class EffectPolicy:
    scopes: tuple[str, ...]
    approvals: tuple[str, ...]
    always_denied: bool


@dataclass(frozen=True)
class Constitution:
    schema: str
    policy_epoch: str
    source_revision: str
    immutable_denials: tuple[str, ...]
    effects: Mapping[str, EffectPolicy]
    digest: str


@dataclass(frozen=True)
class AuthorityEnvelope:
    schema: str
    authority_id: str
    actor_id: str
    subject_id: str
    effect: str
    scopes: tuple[str, ...]
    approvals: tuple[str, ...]
    preserved_denials: tuple[str, ...]
    requested_capabilities: tuple[str, ...]
    expires_at: datetime


@dataclass(frozen=True)
class CausalIdentity:
    schema: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    mission_id: str | None
    actor_id: str
    policy_epoch: str
    source_revision: str


@dataclass(frozen=True)
class PolicyDecision:
    status: DecisionStatus
    allowed: bool
    reason_codes: tuple[str, ...]
    missing_scopes: tuple[str, ...]
    missing_approvals: tuple[str, ...]
    missing_preserved_denials: tuple[str, ...]
    requested_immutable_denials: tuple[str, ...]
    constitution_digest: str
    authority_digest: str


def canonical_json_bytes(value: object) -> bytes:
    """Return the one canonical JSON encoding used for policy evidence."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    """Hash a value after canonical JSON encoding."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def parse_utc_timestamp(value: str, context: str) -> datetime:
    """Parse a timezone-aware ISO-8601 timestamp and normalize it to UTC."""

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{context} must be a valid ISO-8601 timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{context} must include an explicit timezone: {value}")
    return parsed.astimezone(timezone.utc)


def format_utc_timestamp(value: datetime, context: str) -> str:
    """Format an aware timestamp in the canonical UTC representation."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{context} must include an explicit timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


def load_strict_json(path: Path) -> Mapping[str, object]:
    """Load a JSON object while rejecting duplicate keys and malformed roots."""

    if not path.is_file():
        raise FileNotFoundError(f"required policy file is missing: {path}")
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"policy file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(decoded, dict):
        raise TypeError(f"policy file root must be a JSON object: {path}")
    return cast(Mapping[str, object], decoded)


def _require_exact_keys(value: Mapping[str, object], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(f"{context} keys are invalid; missing={missing}; unknown={unknown}")


def _require_string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{context} must be a non-empty string")
    return value


def _require_identifier(value: object, context: str) -> str:
    identifier = _require_string(value, context)
    if _IDENTIFIER_PATTERN.fullmatch(identifier) is None:
        raise ValueError(f"{context} has an invalid identifier: {identifier}")
    return identifier


def _require_optional_identifier(value: object, context: str) -> str | None:
    if value is None:
        return None
    return _require_identifier(value, context)


def _require_string_tuple(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{context} must be an array of strings")
    parsed = tuple(_require_identifier(item, f"{context}[{index}]") for index, item in enumerate(value))
    if len(parsed) != len(set(parsed)):
        raise ValueError(f"{context} must not contain duplicate values")
    return tuple(sorted(parsed))


def _require_mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{context} must be a JSON object")
    return cast(Mapping[str, object], value)


def parse_constitution(value: Mapping[str, object]) -> Constitution:
    """Strictly parse the versioned constitution contract."""

    _require_exact_keys(
        value,
        {"schema", "policyEpoch", "sourceRevision", "immutableDenials", "effects"},
        "constitution",
    )
    raw_effects = _require_mapping(value["effects"], "constitution.effects")
    effects: dict[str, EffectPolicy] = {}
    for raw_name, raw_policy in raw_effects.items():
        name = _require_identifier(raw_name, "constitution.effects key")
        policy = _require_mapping(raw_policy, f"constitution.effects.{name}")
        _require_exact_keys(policy, {"scopes", "approvals", "alwaysDenied"}, f"constitution.effects.{name}")
        always_denied = policy["alwaysDenied"]
        if not isinstance(always_denied, bool):
            raise TypeError(f"constitution.effects.{name}.alwaysDenied must be a boolean")
        effects[name] = EffectPolicy(
            scopes=_require_string_tuple(policy["scopes"], f"constitution.effects.{name}.scopes"),
            approvals=_require_string_tuple(policy["approvals"], f"constitution.effects.{name}.approvals"),
            always_denied=always_denied,
        )
    if not effects:
        raise ValueError("constitution.effects must define at least one effect")
    schema = _require_identifier(value["schema"], "constitution.schema")
    if schema != "blunder.constitution.v1":
        raise ValueError(f"constitution.schema is unsupported: {schema}")
    return Constitution(
        schema=schema,
        policy_epoch=_require_identifier(value["policyEpoch"], "constitution.policyEpoch"),
        source_revision=_require_identifier(value["sourceRevision"], "constitution.sourceRevision"),
        immutable_denials=_require_string_tuple(value["immutableDenials"], "constitution.immutableDenials"),
        effects=effects,
        digest=canonical_sha256(value),
    )


def load_runtime_constitution() -> Constitution:
    """Load the package-owned constitution without an implicit fallback."""

    path = Path(__file__).resolve().parent / "config" / "constitution.v1.json"
    return parse_constitution(load_strict_json(path))


def authority_envelope_to_dict(envelope: AuthorityEnvelope) -> dict[str, object]:
    """Serialize an authority envelope to its canonical external contract."""

    return {
        "schema": envelope.schema,
        "authorityId": envelope.authority_id,
        "actorId": envelope.actor_id,
        "subjectId": envelope.subject_id,
        "effect": envelope.effect,
        "scopes": list(envelope.scopes),
        "approvals": list(envelope.approvals),
        "preservedDenials": list(envelope.preserved_denials),
        "requestedCapabilities": list(envelope.requested_capabilities),
        "expiresAt": format_utc_timestamp(envelope.expires_at, "authority.expiresAt"),
    }


def parse_authority_envelope(value: Mapping[str, object]) -> AuthorityEnvelope:
    """Strictly parse an authority envelope."""

    _require_exact_keys(
        value,
        {
            "schema",
            "authorityId",
            "actorId",
            "subjectId",
            "effect",
            "scopes",
            "approvals",
            "preservedDenials",
            "requestedCapabilities",
            "expiresAt",
        },
        "authority",
    )
    schema = _require_identifier(value["schema"], "authority.schema")
    if schema != "blunder.authority-envelope.v1":
        raise ValueError(f"authority.schema is unsupported: {schema}")
    return AuthorityEnvelope(
        schema=schema,
        authority_id=_require_identifier(value["authorityId"], "authority.authorityId"),
        actor_id=_require_identifier(value["actorId"], "authority.actorId"),
        subject_id=_require_identifier(value["subjectId"], "authority.subjectId"),
        effect=_require_identifier(value["effect"], "authority.effect"),
        scopes=_require_string_tuple(value["scopes"], "authority.scopes"),
        approvals=_require_string_tuple(value["approvals"], "authority.approvals"),
        preserved_denials=_require_string_tuple(value["preservedDenials"], "authority.preservedDenials"),
        requested_capabilities=_require_string_tuple(
            value["requestedCapabilities"], "authority.requestedCapabilities"
        ),
        expires_at=parse_utc_timestamp(_require_string(value["expiresAt"], "authority.expiresAt"), "authority.expiresAt"),
    )


def build_authority_envelope(
    authority_id: str,
    actor_id: str,
    subject_id: str,
    effect: str,
    scopes: Sequence[str],
    approvals: Sequence[str],
    preserved_denials: Sequence[str],
    requested_capabilities: Sequence[str],
    expires_at: datetime,
) -> AuthorityEnvelope:
    """Build and validate an authority envelope from explicit values."""

    raw: dict[str, object] = {
        "schema": "blunder.authority-envelope.v1",
        "authorityId": authority_id,
        "actorId": actor_id,
        "subjectId": subject_id,
        "effect": effect,
        "scopes": list(scopes),
        "approvals": list(approvals),
        "preservedDenials": list(preserved_denials),
        "requestedCapabilities": list(requested_capabilities),
        "expiresAt": format_utc_timestamp(expires_at, "expires_at"),
    }
    return parse_authority_envelope(raw)


def causal_identity_to_dict(identity: CausalIdentity) -> dict[str, object]:
    """Serialize a causal identity."""

    return {
        "schema": identity.schema,
        "traceId": identity.trace_id,
        "spanId": identity.span_id,
        "parentSpanId": identity.parent_span_id,
        "missionId": identity.mission_id,
        "actorId": identity.actor_id,
        "policyEpoch": identity.policy_epoch,
        "sourceRevision": identity.source_revision,
    }


def parse_causal_identity(value: Mapping[str, object]) -> CausalIdentity:
    """Strictly parse a causal identity."""

    _require_exact_keys(
        value,
        {"schema", "traceId", "spanId", "parentSpanId", "missionId", "actorId", "policyEpoch", "sourceRevision"},
        "causalIdentity",
    )
    schema = _require_identifier(value["schema"], "causalIdentity.schema")
    if schema != "blunder.causal-identity.v1":
        raise ValueError(f"causalIdentity.schema is unsupported: {schema}")
    trace_id = _require_string(value["traceId"], "causalIdentity.traceId")
    span_id = _require_string(value["spanId"], "causalIdentity.spanId")
    parent_span_id = value["parentSpanId"]
    if re.fullmatch(r"[0-9a-f]{32}", trace_id) is None:
        raise ValueError("causalIdentity.traceId must be 32 lowercase hexadecimal characters")
    if re.fullmatch(r"[0-9a-f]{16}", span_id) is None:
        raise ValueError("causalIdentity.spanId must be 16 lowercase hexadecimal characters")
    if parent_span_id is not None:
        parent_span_id = _require_string(parent_span_id, "causalIdentity.parentSpanId")
        if re.fullmatch(r"[0-9a-f]{16}", parent_span_id) is None:
            raise ValueError("causalIdentity.parentSpanId must be 16 lowercase hexadecimal characters")
    return CausalIdentity(
        schema=schema,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=cast(str | None, parent_span_id),
        mission_id=_require_optional_identifier(value["missionId"], "causalIdentity.missionId"),
        actor_id=_require_identifier(value["actorId"], "causalIdentity.actorId"),
        policy_epoch=_require_identifier(value["policyEpoch"], "causalIdentity.policyEpoch"),
        source_revision=_require_identifier(value["sourceRevision"], "causalIdentity.sourceRevision"),
    )


def build_causal_identity(
    actor_id: str,
    policy_epoch: str,
    source_revision: str,
    mission_id: str | None,
    parent_span_id: str | None,
    nonce: str,
) -> CausalIdentity:
    """Derive stable trace identifiers from explicit causal inputs."""

    trace_id = canonical_sha256(["trace", actor_id, mission_id, nonce])[:32]
    span_id = canonical_sha256(["span", actor_id, mission_id, parent_span_id, nonce])[:16]
    raw: dict[str, object] = {
        "schema": "blunder.causal-identity.v1",
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": parent_span_id,
        "missionId": mission_id,
        "actorId": actor_id,
        "policyEpoch": policy_epoch,
        "sourceRevision": source_revision,
    }
    return parse_causal_identity(raw)


def policy_decision_to_dict(decision: PolicyDecision) -> dict[str, object]:
    """Serialize a policy decision without authority payload data."""

    return {
        "status": decision.status,
        "allowed": decision.allowed,
        "reasonCodes": list(decision.reason_codes),
        "missingScopes": list(decision.missing_scopes),
        "missingApprovals": list(decision.missing_approvals),
        "missingPreservedDenials": list(decision.missing_preserved_denials),
        "requestedImmutableDenials": list(decision.requested_immutable_denials),
        "constitutionDigest": decision.constitution_digest,
        "authorityDigest": decision.authority_digest,
    }


def evaluate_authority(
    constitution: Constitution,
    envelope: AuthorityEnvelope,
    observed_at: datetime,
) -> PolicyDecision:
    """Evaluate authority using deterministic, denial-first policy ordering."""

    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must include an explicit timezone")
    requested_immutable = tuple(sorted(set(envelope.requested_capabilities) & set(constitution.immutable_denials)))
    missing_preserved = tuple(sorted(set(constitution.immutable_denials) - set(envelope.preserved_denials)))
    policy = constitution.effects.get(envelope.effect)
    missing_scopes: tuple[str, ...] = ()
    missing_approvals: tuple[str, ...] = ()
    reasons: list[str] = []
    if requested_immutable:
        reasons.append("REQUESTED_IMMUTABLE_DENIAL")
    if missing_preserved:
        reasons.append("IMMUTABLE_DENIAL_NOT_PRESERVED")
    if policy is None:
        reasons.append("UNKNOWN_EFFECT")
    else:
        if policy.always_denied:
            reasons.append("EFFECT_ALWAYS_DENIED")
        missing_scopes = tuple(sorted(set(policy.scopes) - set(envelope.scopes)))
        if missing_scopes:
            reasons.append("MISSING_REQUIRED_SCOPE")
        missing_approvals = tuple(sorted(set(policy.approvals) - set(envelope.approvals)))
        if missing_approvals:
            reasons.append("MISSING_REQUIRED_APPROVAL")
    if observed_at.astimezone(timezone.utc) >= envelope.expires_at:
        reasons.append("AUTHORITY_EXPIRED")
    allowed = not reasons
    return PolicyDecision(
        status="ALLOW" if allowed else "DENY",
        allowed=allowed,
        reason_codes=tuple(reasons) if reasons else ("AUTHORITY_VALID",),
        missing_scopes=missing_scopes,
        missing_approvals=missing_approvals,
        missing_preserved_denials=missing_preserved,
        requested_immutable_denials=requested_immutable,
        constitution_digest=constitution.digest,
        authority_digest=canonical_sha256(authority_envelope_to_dict(envelope)),
    )
