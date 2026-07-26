"""Content-addressed constitutional amendment proposals without activation authority."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Mapping, Sequence, cast

from blunder.constitutional_adapters import record_root_policy_activation_denial
from blunder.constitutional_kernel import (
    Constitution,
    EffectPolicy,
    canonical_sha256,
    format_utc_timestamp,
    load_runtime_constitution,
    parse_utc_timestamp,
)


AmendmentOperation = Literal["ADD", "REPLACE"]
AMENDMENT_SCHEMA = "blunder.constitution-amendment-proposal.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class EffectAmendment:
    operation: AmendmentOperation
    effect: str
    policy: EffectPolicy


@dataclass(frozen=True)
class ConstitutionAmendmentProposal:
    schema: str
    proposal_id: str
    base_constitution_digest: str
    proposer_id: str
    proposed_policy_epoch: str
    created_at: datetime
    summary: str
    evidence_digests: tuple[str, ...]
    preserved_immutable_denials: tuple[str, ...]
    effect_changes: tuple[EffectAmendment, ...]
    activation_authorized: bool
    proposal_digest: str


def _exact_keys(value: Mapping[str, object], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{context} keys are invalid; missing={sorted(expected - actual)}; unknown={sorted(actual - expected)}"
        )


def _identifier(value: object, context: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{context} must be a valid identifier")
    return value


def _digest(value: object, context: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{context} must be a lowercase SHA-256 digest")
    return value


def _identifier_tuple(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{context} must be an array")
    parsed = tuple(_identifier(item, f"{context}[{index}]") for index, item in enumerate(value))
    if len(parsed) != len(set(parsed)):
        raise ValueError(f"{context} must not contain duplicates")
    if parsed != tuple(sorted(parsed)):
        raise ValueError(f"{context} must use canonical sorted order")
    return parsed


def _digest_tuple(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{context} must be an array")
    parsed = tuple(_digest(item, f"{context}[{index}]") for index, item in enumerate(value))
    if not parsed or len(parsed) > 64 or len(parsed) != len(set(parsed)):
        raise ValueError(f"{context} must contain one through 64 unique evidence digests")
    if parsed != tuple(sorted(parsed)):
        raise ValueError(f"{context} must use canonical sorted order")
    return parsed


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{context} must be an object")
    return cast(Mapping[str, object], value)


def _effect_policy(value: object, context: str) -> EffectPolicy:
    policy = _mapping(value, context)
    _exact_keys(policy, {"scopes", "approvals", "alwaysDenied"}, context)
    always_denied = policy["alwaysDenied"]
    if not isinstance(always_denied, bool):
        raise TypeError(f"{context}.alwaysDenied must be a boolean")
    return EffectPolicy(
        scopes=_identifier_tuple(policy["scopes"], f"{context}.scopes"),
        approvals=_identifier_tuple(policy["approvals"], f"{context}.approvals"),
        always_denied=always_denied,
    )


def _effect_change(value: object, index: int, constitution: Constitution) -> EffectAmendment:
    context = f"amendment.effectChanges[{index}]"
    change = _mapping(value, context)
    _exact_keys(change, {"operation", "effect", "policy"}, context)
    operation = change["operation"]
    if operation not in {"ADD", "REPLACE"}:
        raise ValueError(f"{context}.operation must be ADD or REPLACE")
    effect = _identifier(change["effect"], f"{context}.effect")
    if operation == "ADD" and effect in constitution.effects:
        raise ValueError(f"{context} cannot ADD an existing effect: {effect}")
    if operation == "REPLACE" and effect not in constitution.effects:
        raise ValueError(f"{context} cannot REPLACE an unknown effect: {effect}")
    return EffectAmendment(cast(AmendmentOperation, operation), effect, _effect_policy(change["policy"], f"{context}.policy"))


def amendment_proposal_to_dict(proposal: ConstitutionAmendmentProposal) -> dict[str, object]:
    """Serialize the complete content-addressed proposal contract."""

    return {
        "schema": proposal.schema,
        "proposalId": proposal.proposal_id,
        "baseConstitutionDigest": proposal.base_constitution_digest,
        "proposerId": proposal.proposer_id,
        "proposedPolicyEpoch": proposal.proposed_policy_epoch,
        "createdAt": format_utc_timestamp(proposal.created_at, "amendment.createdAt"),
        "summary": proposal.summary,
        "evidenceDigests": list(proposal.evidence_digests),
        "preservedImmutableDenials": list(proposal.preserved_immutable_denials),
        "effectChanges": [
            {
                "operation": change.operation,
                "effect": change.effect,
                "policy": {
                    "scopes": list(change.policy.scopes),
                    "approvals": list(change.policy.approvals),
                    "alwaysDenied": change.policy.always_denied,
                },
            }
            for change in proposal.effect_changes
        ],
        "activationAuthorized": proposal.activation_authorized,
        "proposalDigest": proposal.proposal_digest,
    }


def parse_amendment_proposal(
    value: Mapping[str, object],
    constitution: Constitution,
) -> ConstitutionAmendmentProposal:
    """Strictly parse a proposal and prove that it preserves root invariants."""

    expected = {
        "schema",
        "proposalId",
        "baseConstitutionDigest",
        "proposerId",
        "proposedPolicyEpoch",
        "createdAt",
        "summary",
        "evidenceDigests",
        "preservedImmutableDenials",
        "effectChanges",
        "activationAuthorized",
        "proposalDigest",
    }
    _exact_keys(value, expected, "amendment")
    if value["schema"] != AMENDMENT_SCHEMA:
        raise ValueError(f"amendment.schema is unsupported: {value['schema']}")
    base_digest = _digest(value["baseConstitutionDigest"], "amendment.baseConstitutionDigest")
    if base_digest != constitution.digest:
        raise ValueError(
            "amendment base constitution does not match runtime policy: "
            f"proposal={base_digest}; runtime={constitution.digest}"
        )
    preserved = _identifier_tuple(value["preservedImmutableDenials"], "amendment.preservedImmutableDenials")
    if preserved != constitution.immutable_denials:
        raise ValueError("amendment must preserve every immutable denial exactly")
    if value["activationAuthorized"] is not False:
        raise ValueError("an amendment proposal cannot carry activation authority")
    summary = value["summary"]
    if (
        not isinstance(summary, str)
        or summary != summary.strip()
        or len(summary) < 12
        or len(summary) > 1_000
    ):
        raise ValueError("amendment.summary must contain 12 through 1,000 characters")
    raw_changes = value["effectChanges"]
    if not isinstance(raw_changes, list) or not raw_changes or len(raw_changes) > 32:
        raise ValueError("amendment.effectChanges must contain one through 32 changes")
    changes = tuple(_effect_change(item, index, constitution) for index, item in enumerate(raw_changes))
    if len({change.effect for change in changes}) != len(changes):
        raise ValueError("amendment.effectChanges must target unique effects")
    claimed_digest = _digest(value["proposalDigest"], "amendment.proposalDigest")
    unsigned = {key: item for key, item in value.items() if key != "proposalDigest"}
    actual_digest = canonical_sha256(unsigned)
    if claimed_digest != actual_digest:
        raise ValueError(
            f"amendment proposal digest mismatch: claimed={claimed_digest}; actual={actual_digest}"
        )
    return ConstitutionAmendmentProposal(
        schema=AMENDMENT_SCHEMA,
        proposal_id=_identifier(value["proposalId"], "amendment.proposalId"),
        base_constitution_digest=base_digest,
        proposer_id=_identifier(value["proposerId"], "amendment.proposerId"),
        proposed_policy_epoch=_identifier(value["proposedPolicyEpoch"], "amendment.proposedPolicyEpoch"),
        created_at=parse_utc_timestamp(str(value["createdAt"]), "amendment.createdAt"),
        summary=summary,
        evidence_digests=_digest_tuple(value["evidenceDigests"], "amendment.evidenceDigests"),
        preserved_immutable_denials=preserved,
        effect_changes=changes,
        activation_authorized=False,
        proposal_digest=claimed_digest,
    )


def build_amendment_proposal(
    constitution: Constitution,
    proposer_id: str,
    proposed_policy_epoch: str,
    summary: str,
    evidence_digests: Sequence[str],
    effect_changes: Sequence[EffectAmendment],
    created_at: datetime,
    nonce: str,
) -> ConstitutionAmendmentProposal:
    """Build a strict proposal whose digest binds every field except itself."""

    proposal_id = f"amendment-{canonical_sha256([proposer_id, proposed_policy_epoch, nonce])[:32]}"
    unsigned: dict[str, object] = {
        "schema": AMENDMENT_SCHEMA,
        "proposalId": proposal_id,
        "baseConstitutionDigest": constitution.digest,
        "proposerId": proposer_id,
        "proposedPolicyEpoch": proposed_policy_epoch,
        "createdAt": format_utc_timestamp(created_at, "created_at"),
        "summary": summary.strip(),
        "evidenceDigests": sorted(evidence_digests),
        "preservedImmutableDenials": list(constitution.immutable_denials),
        "effectChanges": [
            {
                "operation": change.operation,
                "effect": change.effect,
                "policy": {
                    "scopes": sorted(change.policy.scopes),
                    "approvals": sorted(change.policy.approvals),
                    "alwaysDenied": change.policy.always_denied,
                },
            }
            for change in effect_changes
        ],
        "activationAuthorized": False,
    }
    return parse_amendment_proposal(
        {**unsigned, "proposalDigest": canonical_sha256(unsigned)},
        constitution,
    )


def write_amendment_proposal(
    root: Path,
    proposal: ConstitutionAmendmentProposal,
    observed_at: datetime,
) -> Path:
    """Persist a proposal artifact while recording that activation stays denied."""

    validated = parse_amendment_proposal(amendment_proposal_to_dict(proposal), load_runtime_constitution())
    record_root_policy_activation_denial(root, validated.proposal_id, observed_at)
    destination = (
        root.resolve()
        / "artifacts"
        / "obtuse"
        / "blunder-constitutional-kernel-v1"
        / "amendments"
        / f"{validated.proposal_id}.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(amendment_proposal_to_dict(validated), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination
