"""Redacted append-only shadow policy evidence for Blunder."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence, cast

from blunder.constitutional_kernel import (
    AuthorityEnvelope,
    CausalIdentity,
    canonical_json_bytes,
    canonical_sha256,
    evaluate_authority,
    format_utc_timestamp,
    load_runtime_constitution,
    policy_decision_to_dict,
)


EVENT_SCHEMA = "blunder.cognitive-policy-event.v1"
LEDGER_RELATIVE_PATH = Path("artifacts") / "obtuse" / "blunder-constitutional-kernel-v1" / "decisions.jsonl"


class ConstitutionalPolicyError(PermissionError):
    """Raised after a fail-closed constitutional disagreement is recorded."""


def decision_ledger_path(root: Path) -> Path:
    """Return the runtime-owned path for constitutional shadow evidence."""

    return root.resolve() / LEDGER_RELATIVE_PATH


def _redacted_hash(value: str) -> str:
    return canonical_sha256(["redacted", value])


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate constitutional ledger key is not allowed: {key}")
        result[key] = value
    return result


def _parity_classification(current_allowed: bool, constitutional_allowed: bool) -> str:
    if current_allowed and constitutional_allowed:
        return "MATCH_ALLOW"
    if not current_allowed and not constitutional_allowed:
        return "MATCH_DENY"
    if current_allowed:
        return "CURRENT_ALLOW_CONSTITUTION_DENY"
    return "CURRENT_DENY_CONSTITUTION_ALLOW"


def _build_event(
    envelope: AuthorityEnvelope,
    identity: CausalIdentity,
    current_allowed: bool,
    current_reasons: Sequence[str],
    observed_at: datetime,
) -> dict[str, object]:
    constitution = load_runtime_constitution()
    if identity.policy_epoch != constitution.policy_epoch:
        raise ValueError(
            "causal identity policy epoch does not match the runtime constitution: "
            f"identity={identity.policy_epoch}; constitution={constitution.policy_epoch}"
        )
    if identity.source_revision != constitution.source_revision:
        raise ValueError(
            "causal identity source revision does not match the runtime constitution: "
            f"identity={identity.source_revision}; constitution={constitution.source_revision}"
        )
    decision = evaluate_authority(constitution, envelope, observed_at)
    decision_payload = policy_decision_to_dict(decision)
    core: dict[str, object] = {
        "schema": EVENT_SCHEMA,
        "recordedAt": format_utc_timestamp(observed_at, "observed_at"),
        "effect": envelope.effect,
        "policyEpoch": identity.policy_epoch,
        "sourceRevision": identity.source_revision,
        "causalIdentity": {
            "traceId": identity.trace_id,
            "spanId": identity.span_id,
            "parentSpanId": identity.parent_span_id,
            "missionHash": None if identity.mission_id is None else _redacted_hash(identity.mission_id),
        },
        "authorityHash": _redacted_hash(envelope.authority_id),
        "actorHash": _redacted_hash(envelope.actor_id),
        "subjectHash": _redacted_hash(envelope.subject_id),
        "decision": decision_payload,
        "currentBehavior": {
            "allowed": current_allowed,
            "reasonCount": len(current_reasons),
            "reasonDigests": sorted(_redacted_hash(reason) for reason in current_reasons),
        },
        "parity": _parity_classification(current_allowed, decision.allowed),
        "decisionDigest": canonical_sha256(decision_payload),
    }
    event_id = canonical_sha256(core)[:24]
    payload = {**core, "eventId": event_id}
    return {**payload, "eventDigest": canonical_sha256(payload)}


def _append_event(path: Path, event: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = canonical_json_bytes(event) + b"\n"
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        written = os.write(descriptor, encoded)
        if written != len(encoded):
            raise OSError(f"short append to constitutional decision ledger: path={path}; bytes={written}/{len(encoded)}")
    finally:
        os.close(descriptor)


def record_shadow_decision(
    root: Path,
    envelope: AuthorityEnvelope,
    identity: CausalIdentity,
    current_allowed: bool,
    current_reasons: Sequence[str],
    observed_at: datetime,
) -> dict[str, object]:
    """Evaluate and append one redacted shadow policy event."""

    event = _build_event(envelope, identity, current_allowed, current_reasons, observed_at)
    _append_event(decision_ledger_path(root), event)
    return event


def record_fail_closed_decision(
    root: Path,
    envelope: AuthorityEnvelope,
    identity: CausalIdentity,
    current_allowed: bool,
    current_reasons: Sequence[str],
    observed_at: datetime,
) -> dict[str, object]:
    """Record a decision and block when the current guard is more permissive."""

    event = _build_event(envelope, identity, current_allowed, current_reasons, observed_at)
    _append_event(decision_ledger_path(root), event)
    decision = event.get("decision")
    if not isinstance(decision, dict):
        raise TypeError("constitutional event decision must be an object")
    if current_allowed and decision.get("allowed") is not True:
        reason_codes = decision.get("reasonCodes")
        raise ConstitutionalPolicyError(
            "constitutional policy denied an effect allowed by the current guard: "
            f"effect={envelope.effect}; eventId={event['eventId']}; reasonCodes={reason_codes}"
        )
    return event


def decision_summary(event: Mapping[str, object], mode: str) -> dict[str, object]:
    """Return bounded decision data safe to embed in existing reports."""

    decision = event.get("decision")
    if not isinstance(decision, dict):
        raise TypeError("constitutional event decision must be an object")
    if mode not in {"shadow", "fail_closed"}:
        raise ValueError(f"constitutional decision summary mode is unsupported: {mode}")
    return {
        "mode": mode,
        "eventId": event["eventId"],
        "effect": event["effect"],
        "status": decision["status"],
        "allowed": decision["allowed"],
        "reasonCodes": decision["reasonCodes"],
        "parity": event["parity"],
        "constitutionDigest": decision["constitutionDigest"],
        "authorityDigest": decision["authorityDigest"],
    }


def shadow_summary(event: Mapping[str, object]) -> dict[str, object]:
    """Return the compatibility summary for a measured shadow decision."""

    return decision_summary(event, "shadow")


def fail_closed_summary(event: Mapping[str, object]) -> dict[str, object]:
    """Return the summary for a decision enforced on permissive disagreement."""

    return decision_summary(event, "fail_closed")


def _validate_event(value: object, path: Path, line_number: int) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"constitutional ledger event must be an object: path={path}; line={line_number}")
    event = cast(dict[str, object], value)
    expected = {
        "schema",
        "recordedAt",
        "effect",
        "policyEpoch",
        "sourceRevision",
        "causalIdentity",
        "authorityHash",
        "actorHash",
        "subjectHash",
        "decision",
        "currentBehavior",
        "parity",
        "decisionDigest",
        "eventId",
        "eventDigest",
    }
    if set(event) != expected:
        raise ValueError(
            f"constitutional ledger event keys are invalid: path={path}; line={line_number}; keys={sorted(event)}"
        )
    if event["schema"] != EVENT_SCHEMA:
        raise ValueError(f"constitutional ledger event schema is invalid: path={path}; line={line_number}")
    claimed_digest = event["eventDigest"]
    payload = {key: item for key, item in event.items() if key != "eventDigest"}
    actual_digest = canonical_sha256(payload)
    if claimed_digest != actual_digest:
        raise ValueError(
            f"constitutional ledger event digest mismatch: path={path}; line={line_number}; "
            f"claimed={claimed_digest}; actual={actual_digest}"
        )
    decision = event["decision"]
    if not isinstance(decision, dict) or event["decisionDigest"] != canonical_sha256(decision):
        raise ValueError(f"constitutional ledger decision digest mismatch: path={path}; line={line_number}")
    return event


def load_decision_events(root: Path) -> tuple[Mapping[str, object], ...]:
    """Read and integrity-check every event in the append-only ledger."""

    path = decision_ledger_path(root)
    if not path.is_file():
        raise FileNotFoundError(f"constitutional decision ledger is missing: {path}")
    events: list[Mapping[str, object]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            raise ValueError(f"constitutional decision ledger contains a blank line: path={path}; line={line_number}")
        try:
            decoded = json.loads(raw_line, object_pairs_hook=_reject_duplicate_keys)
        except json.JSONDecodeError as exc:
            raise ValueError(f"constitutional decision ledger has invalid JSON: path={path}; line={line_number}") from exc
        events.append(_validate_event(decoded, path, line_number))
    if not events:
        raise ValueError(f"constitutional decision ledger contains no events: {path}")
    return tuple(events)


def build_parity_report(events: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Summarize shadow parity while retaining every disagreement identifier."""

    if not events:
        raise ValueError("cannot build a constitutional parity report without events")
    counts: dict[str, int] = {
        "MATCH_ALLOW": 0,
        "MATCH_DENY": 0,
        "CURRENT_ALLOW_CONSTITUTION_DENY": 0,
        "CURRENT_DENY_CONSTITUTION_ALLOW": 0,
    }
    disagreements: list[dict[str, object]] = []
    effects: set[str] = set()
    for event in events:
        parity = event.get("parity")
        if not isinstance(parity, str) or parity not in counts:
            raise ValueError(f"constitutional event contains an invalid parity classification: {parity}")
        counts[parity] += 1
        effect = event.get("effect")
        if not isinstance(effect, str):
            raise TypeError("constitutional event effect must be a string")
        effects.add(effect)
        if parity.startswith("CURRENT_"):
            decision = event.get("decision")
            if not isinstance(decision, dict):
                raise TypeError("constitutional event decision must be an object")
            disagreements.append(
                {
                    "eventId": event["eventId"],
                    "effect": effect,
                    "parity": parity,
                    "constitutionalStatus": decision["status"],
                    "reasonCodes": decision["reasonCodes"],
                }
            )
    return {
        "schema": "blunder.constitutional-parity-report.v1",
        "status": "PASS" if not disagreements else "SHADOW_DISAGREEMENT",
        "eventCount": len(events),
        "effectCount": len(effects),
        "effects": sorted(effects),
        "counts": counts,
        "disagreementCount": len(disagreements),
        "disagreements": disagreements,
        "ledgerDigest": canonical_sha256(list(events)),
    }
