"""Candidate-only research intake and fail-closed allocator client.

The Research Mesh is deliberately narrower than the Fund control plane:

* Doofus and Waterboy artifacts are immutable research inputs, not authority.
* The mesh does not evaluate evidence, issue passports, or promote strategies.
* Only a Doofus bundle can provide the exact unsigned passport body from which
  a singleton capital request is derived.
* Waterboy forecasts may support a request, but can never select a strategy or
  grant capital.
* Core independently resolves all signed evidence when it accepts the
  allocator-signed request.  Acceptance is not a capital lease.
* A POST is never retried after dispatch because its outcome may be ambiguous.

This module has no broker or model-provider integration and accepts its HTTP
transport, clock, signer, token, and filesystem roots as injected values.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
from collections.abc import Callable, Mapping as ABCMapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol, cast

from blunder.fund.canonical import (
    canonical_json_bytes,
    canonical_sha256,
    format_utc,
    parse_utc,
    require_digest,
    require_identifier,
    require_positive_int,
    require_sorted_unique,
)
from blunder.fund.contracts import (
    AlphaPassportV1,
    CapitalRequestV1,
    EvidenceVerdictV1,
    ReadinessDescriptorV1,
    SignedEnvelopeV1,
    Venue,
    validate_authorized_instrument,
)
from blunder.fund.crypto import Ed25519Keyring, EnvelopeSigner


DOOFUS_BUNDLE_SCHEMA = "dumbmoney.doofus_fund_interop_bundle.v1"
WATERBOY_FORECAST_SCHEMA = "dumbmoney.sports-forecast-envelope.v1"
ALLOCATION_PLAN_SCHEMA = "dumbmoney.research-mesh-allocation-plan.v1"
CORE_APPEND_RESULT_SCHEMA = "dumbmoney.event-append-result.v1"
CORE_SOURCE_ID = "DumbMoneyCore"
ALLOCATOR_SOURCE_ID = "DumbMoneyResearchMeshAllocator"
ALLOCATOR_APPEND_PATH = "/v1/allocator/events:append"
CAPITAL_REQUEST_TTL = timedelta(seconds=60)
MAX_PLAN_WINDOW = timedelta(hours=24)
DEFAULT_MAX_INPUT_BYTES = 1_048_576
DEFAULT_MAX_RESPONSE_BYTES = 262_144


class ResearchMeshError(RuntimeError):
    """Base class for bounded Research Mesh failures."""


class CandidateValidationError(ValueError):
    """A candidate is malformed or violates its candidate-only boundary."""


class AllocationPlanError(ValueError):
    """An allocation plan is malformed or cannot bind to accepted research."""


class CoreReadinessError(ResearchMeshError):
    """Core's signed readiness cannot support a new allocator request."""


class CoreTransportError(ResearchMeshError):
    """A request failed before a deterministic Core response was available."""


class AmbiguousCoreSubmission(ResearchMeshError):
    """A dispatched allocator POST cannot safely be retried."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _validate_research_json_domain(value: object, context: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{context} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_research_json_domain(item, f"{context}[{index}]")
        return
    if isinstance(value, ABCMapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{context} contains a non-string key")
            _validate_research_json_domain(item, f"{context}.{key}")
        return
    raise TypeError(f"{context} contains unsupported type {type(value).__name__}")


def loads_research_json(raw: bytes, context: str) -> Mapping[str, object]:
    """Load one strict JSON object while retaining finite forecast floats."""

    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{context} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise TypeError(f"{context} root must be an object")
    _validate_research_json_domain(value)
    return cast(Mapping[str, object], value)


def canonical_research_json_bytes(value: object) -> bytes:
    """Canonical JSON for candidate formats that legitimately contain floats."""

    _validate_research_json_domain(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_research_sha256(value: object) -> str:
    return hashlib.sha256(canonical_research_json_bytes(value)).hexdigest()


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, ABCMapping):
        raise TypeError(f"{context} must be an object")
    return cast(Mapping[str, object], value)


def _exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    context: str,
) -> None:
    observed = set(value)
    if observed != expected:
        raise ValueError(
            f"{context} keys are invalid; "
            f"missing={sorted(expected - observed)}; "
            f"unknown={sorted(observed - expected)}"
        )


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{context} must be a non-empty string")
    return value


def _boolean(value: object, expected: bool, context: str) -> None:
    if not isinstance(value, bool) or value is not expected:
        raise ValueError(f"{context} must be {str(expected).lower()}")


def _integer(
    value: object,
    context: str,
    *,
    minimum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{context} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{context} must be at least {minimum}")
    return value


def _number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{context} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{context} must be a finite number")
    return result


def _string_array(
    value: object,
    context: str,
    *,
    nonempty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise TypeError(f"{context} must be an array of non-empty strings")
    result = tuple(cast(list[str], value))
    if nonempty and not result:
        raise ValueError(f"{context} must not be empty")
    return result


def _parse_offset_utc(value: object, context: str) -> datetime:
    text = _string(value, context)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{context} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{context} must include an explicit timezone")
    if parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{context} must use UTC")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ValidatedCandidate:
    """Content-addressed candidate metadata safe to persist in the mesh."""

    candidate_digest: str
    source_kind: str
    schema: str
    as_of: datetime
    expires_at: datetime
    passport_digest: str | None = None
    strategy_hash: str | None = None
    venue: Venue | None = None
    intended_instruments: tuple[str, ...] = ()
    maximum_loss_cents: int | None = None

    def __post_init__(self) -> None:
        require_digest(self.candidate_digest, "candidate_digest")
        if self.source_kind not in {"doofus", "waterboy"}:
            raise ValueError("source_kind must be doofus or waterboy")
        if self.expires_at <= self.as_of:
            raise ValueError("candidate expires_at must be after as_of")
        doofus_fields = (
            self.passport_digest,
            self.strategy_hash,
            self.venue,
            self.maximum_loss_cents,
        )
        if self.source_kind == "doofus":
            if any(item is None for item in doofus_fields):
                raise ValueError("Doofus candidate is missing passport authority metadata")
            require_digest(cast(str, self.passport_digest), "passport_digest")
            require_digest(cast(str, self.strategy_hash), "strategy_hash")
            require_sorted_unique(self.intended_instruments, "intended_instruments")
            require_positive_int(
                cast(int, self.maximum_loss_cents),
                "maximum_loss_cents",
            )
        elif any(item is not None for item in doofus_fields) or self.intended_instruments:
            raise ValueError("Waterboy candidate cannot carry passport authority metadata")


def validate_doofus_candidate(
    value: Mapping[str, object],
    *,
    observed_at: datetime,
) -> ValidatedCandidate:
    """Validate Doofus's exact unsigned Fund interoperability bundle."""

    _exact_keys(
        value,
        {
            "schema_id",
            "alpha_passport",
            "evidence_verdicts",
            "signed",
            "grants_live_authority",
            "grants_capital_authority",
        },
        "Doofus bundle",
    )
    if value["schema_id"] != DOOFUS_BUNDLE_SCHEMA:
        raise CandidateValidationError("unsupported Doofus bundle schema")
    _boolean(value["signed"], False, "Doofus bundle signed")
    _boolean(
        value["grants_live_authority"],
        False,
        "Doofus bundle grants_live_authority",
    )
    _boolean(
        value["grants_capital_authority"],
        False,
        "Doofus bundle grants_capital_authority",
    )
    try:
        passport = AlphaPassportV1.from_dict(
            _mapping(value["alpha_passport"], "Doofus alpha_passport")
        )
    except (TypeError, ValueError) as exc:
        raise CandidateValidationError(
            f"invalid Doofus alpha passport: {exc}"
        ) from exc
    if passport.evidence_verdict_hashes:
        raise CandidateValidationError(
            "unsigned Doofus passport must not claim signed evidence verdicts"
        )
    raw_verdicts = value["evidence_verdicts"]
    if not isinstance(raw_verdicts, list) or len(raw_verdicts) != 4:
        raise CandidateValidationError(
            "Doofus bundle must contain exactly four court candidates"
        )
    passport_digest = canonical_sha256(passport.to_dict())
    courts: set[str] = set()
    for index, raw_verdict in enumerate(raw_verdicts):
        try:
            verdict = EvidenceVerdictV1.from_dict(
                _mapping(
                    raw_verdict,
                    f"Doofus evidence_verdicts[{index}]",
                )
            )
        except (TypeError, ValueError) as exc:
            raise CandidateValidationError(
                f"invalid Doofus evidence verdict {index}: {exc}"
            ) from exc
        if verdict.passport_digest != passport_digest:
            raise CandidateValidationError(
                "Doofus evidence verdict does not bind the bundled passport"
            )
        if verdict.evidence_class is not passport.evidence_class:
            raise CandidateValidationError(
                "Doofus evidence class does not match the bundled passport"
            )
        if verdict.evaluated_at < passport.created_at:
            raise CandidateValidationError(
                "Doofus evidence verdict predates the bundled passport"
            )
        if verdict.expires_at > passport.expires_at:
            raise CandidateValidationError(
                "Doofus evidence verdict outlives the bundled passport"
            )
        if verdict.evaluated_at > observed_at:
            raise CandidateValidationError(
                "Doofus evidence verdict is future-dated"
            )
        courts.add(verdict.court)
    if courts != {
        "integrity",
        "statistics",
        "economics",
        "adversarial_operations",
    }:
        raise CandidateValidationError(
            "Doofus bundle must contain every deterministic court exactly once"
        )
    if passport.created_at > observed_at:
        raise CandidateValidationError("Doofus passport is future-dated")
    candidate_digest = canonical_research_sha256(value)
    return ValidatedCandidate(
        candidate_digest=candidate_digest,
        source_kind="doofus",
        schema=DOOFUS_BUNDLE_SCHEMA,
        as_of=passport.created_at,
        expires_at=passport.expires_at,
        passport_digest=passport_digest,
        strategy_hash=passport.strategy_hash,
        venue=passport.venue,
        intended_instruments=passport.intended_instruments,
        maximum_loss_cents=passport.maximum_loss_cents,
    )


def _validate_score_distribution(value: object, context: str) -> None:
    item = _mapping(value, context)
    _exact_keys(item, {"mean", "lower", "upper", "interval_mass"}, context)
    mean = _number(item["mean"], f"{context}.mean")
    lower = _integer(item["lower"], f"{context}.lower")
    upper = _integer(item["upper"], f"{context}.upper")
    mass = _number(item["interval_mass"], f"{context}.interval_mass")
    if lower > upper or not lower <= mean <= upper:
        raise CandidateValidationError(f"{context} interval does not contain its mean")
    if not 0 < mass <= 1:
        raise CandidateValidationError(f"{context}.interval_mass must be in (0, 1]")


def validate_waterboy_candidate(
    value: Mapping[str, object],
    *,
    observed_at: datetime,
) -> ValidatedCandidate:
    """Validate Waterboy's exact candidate-only, point-in-time envelope."""

    expected = {
        "schema",
        "candidate_only",
        "order_authority",
        "capital_authority",
        "canonical_digest",
        "as_of",
        "expires_at",
        "observation_set_digest",
        "target",
        "model",
        "distributions",
        "confidence_status",
        "calibration_status",
        "evidence_status",
        "freshness_status",
        "provenance",
        "data_rights",
        "limitations",
    }
    _exact_keys(value, expected, "Waterboy forecast")
    if value["schema"] != WATERBOY_FORECAST_SCHEMA:
        raise CandidateValidationError("unsupported Waterboy forecast schema")
    _boolean(value["candidate_only"], True, "Waterboy candidate_only")
    _boolean(value["order_authority"], False, "Waterboy order_authority")
    _boolean(value["capital_authority"], False, "Waterboy capital_authority")
    supplied_digest = _string(
        value["canonical_digest"],
        "Waterboy canonical_digest",
    )
    require_digest(supplied_digest, "Waterboy canonical_digest")
    digest_body = dict(value)
    digest_body.pop("canonical_digest")
    if canonical_research_sha256(digest_body) != supplied_digest:
        raise CandidateValidationError("Waterboy canonical digest mismatch")

    as_of = _parse_offset_utc(value["as_of"], "Waterboy as_of")
    expires_at = _parse_offset_utc(value["expires_at"], "Waterboy expires_at")
    if expires_at <= as_of:
        raise CandidateValidationError("Waterboy expires_at must be after as_of")
    if as_of > observed_at:
        raise CandidateValidationError("Waterboy forecast is future-dated")
    require_digest(
        _string(
            value["observation_set_digest"],
            "Waterboy observation_set_digest",
        ),
        "Waterboy observation_set_digest",
    )
    for name in (
        "confidence_status",
        "evidence_status",
    ):
        _string(value[name], f"Waterboy {name}")
    if value["calibration_status"] != "provisional_not_independently_calibrated":
        raise CandidateValidationError(
            "Waterboy must not claim independent calibration"
        )
    if value["freshness_status"] != "fresh_as_of_cutoff":
        raise CandidateValidationError(
            "Waterboy freshness status is not point-in-time safe"
        )

    target = _mapping(value["target"], "Waterboy target")
    _exact_keys(
        target,
        {
            "game_id",
            "league",
            "starts_at",
            "home_team_id",
            "away_team_id",
            "home_team_name",
            "away_team_name",
            "neutral_site",
        },
        "Waterboy target",
    )
    for name in (
        "game_id",
        "league",
        "home_team_id",
        "away_team_id",
        "home_team_name",
        "away_team_name",
    ):
        _string(target[name], f"Waterboy target.{name}")
    starts_at = _parse_offset_utc(
        target["starts_at"],
        "Waterboy target.starts_at",
    )
    if starts_at <= as_of or expires_at > starts_at:
        raise CandidateValidationError(
            "Waterboy forecast window must end no later than the future target"
        )
    if not isinstance(target["neutral_site"], bool):
        raise CandidateValidationError(
            "Waterboy target.neutral_site must be a boolean"
        )

    model = _mapping(value["model"], "Waterboy model")
    _exact_keys(
        model,
        {
            "model_id",
            "model_version",
            "champion_id",
            "champion_version",
            "seed",
            "draws",
            "fidelity",
        },
        "Waterboy model",
    )
    for name in (
        "model_id",
        "model_version",
        "champion_id",
        "champion_version",
    ):
        _string(model[name], f"Waterboy model.{name}")
    _integer(model["seed"], "Waterboy model.seed", minimum=0)
    _integer(model["draws"], "Waterboy model.draws", minimum=1)
    if model["fidelity"] != "F0":
        raise CandidateValidationError("Waterboy intake accepts evidence-supported F0 only")

    distributions = _mapping(
        value["distributions"],
        "Waterboy distributions",
    )
    _exact_keys(
        distributions,
        {
            "home_score",
            "away_score",
            "total_score",
            "expected_margin",
            "outcome",
        },
        "Waterboy distributions",
    )
    for name in ("home_score", "away_score", "total_score"):
        _validate_score_distribution(
            distributions[name],
            f"Waterboy distributions.{name}",
        )
    _number(
        distributions["expected_margin"],
        "Waterboy distributions.expected_margin",
    )
    outcome = _mapping(
        distributions["outcome"],
        "Waterboy distributions.outcome",
    )
    _exact_keys(
        outcome,
        {"home_win", "away_win", "tie"},
        "Waterboy distributions.outcome",
    )
    probabilities = tuple(
        _number(outcome[name], f"Waterboy distributions.outcome.{name}")
        for name in ("home_win", "away_win", "tie")
    )
    if any(item < 0 or item > 1 for item in probabilities) or not math.isclose(
        sum(probabilities),
        1.0,
        abs_tol=0.0002,
    ):
        raise CandidateValidationError(
            "Waterboy outcome probabilities must be bounded and sum to one"
        )

    provenance = _mapping(value["provenance"], "Waterboy provenance")
    _exact_keys(
        provenance,
        {"target_revision_id", "observation_count", "sources"},
        "Waterboy provenance",
    )
    require_digest(
        _string(
            provenance["target_revision_id"],
            "Waterboy provenance.target_revision_id",
        ),
        "Waterboy provenance.target_revision_id",
    )
    observation_count = _integer(
        provenance["observation_count"],
        "Waterboy provenance.observation_count",
        minimum=1,
    )
    raw_sources = provenance["sources"]
    if not isinstance(raw_sources, list) or not raw_sources:
        raise CandidateValidationError(
            "Waterboy provenance.sources must not be empty"
        )
    source_count = 0
    source_names: list[str] = []
    for index, raw_source in enumerate(raw_sources):
        context = f"Waterboy provenance.sources[{index}]"
        source = _mapping(raw_source, context)
        _exact_keys(
            source,
            {
                "source",
                "observation_count",
                "latest_observed_at",
                "source_urls",
                "payload_set_digest",
            },
            context,
        )
        source_names.append(_string(source["source"], f"{context}.source"))
        count = _integer(
            source["observation_count"],
            f"{context}.observation_count",
            minimum=1,
        )
        source_count += count
        latest = _parse_offset_utc(
            source["latest_observed_at"],
            f"{context}.latest_observed_at",
        )
        if latest > as_of:
            raise CandidateValidationError(
                "Waterboy source provenance crosses the as-of cutoff"
            )
        source_urls = _string_array(
            source["source_urls"],
            f"{context}.source_urls",
            nonempty=True,
        )
        if source_urls != tuple(sorted(set(source_urls))):
            raise CandidateValidationError(
                "Waterboy source URLs must be sorted and unique"
            )
        require_digest(
            _string(
                source["payload_set_digest"],
                f"{context}.payload_set_digest",
            ),
            f"{context}.payload_set_digest",
        )
    if source_count != observation_count:
        raise CandidateValidationError(
            "Waterboy source observation counts do not match provenance total"
        )
    if source_names != sorted(set(source_names)):
        raise CandidateValidationError(
            "Waterboy provenance sources must be sorted and unique"
        )

    rights = _mapping(value["data_rights"], "Waterboy data_rights")
    _exact_keys(
        rights,
        {
            "status",
            "source_terms_apply",
            "redistribution_status",
            "model_training_status",
            "references",
        },
        "Waterboy data_rights",
    )
    if (
        rights["status"]
        != "source_terms_and_local_authorization_must_be_verified"
        or rights["source_terms_apply"] is not True
        or rights["redistribution_status"] != "not_granted_by_this_envelope"
        or rights["model_training_status"] != "not_granted_by_this_envelope"
    ):
        raise CandidateValidationError(
            "Waterboy data-rights boundary was widened"
        )
    rights_references = _string_array(
        rights["references"],
        "Waterboy data_rights.references",
        nonempty=True,
    )
    if rights_references != tuple(sorted(set(rights_references))):
        raise CandidateValidationError(
            "Waterboy data-rights references must be sorted and unique"
        )
    limitations = _string_array(
        value["limitations"],
        "Waterboy limitations",
        nonempty=True,
    )
    if not any("grants no order or capital authority" in item for item in limitations):
        raise CandidateValidationError(
            "Waterboy candidate-only limitation is missing"
        )
    return ValidatedCandidate(
        candidate_digest=supplied_digest,
        source_kind="waterboy",
        schema=WATERBOY_FORECAST_SCHEMA,
        as_of=as_of,
        expires_at=expires_at,
    )


def validate_candidate(
    value: Mapping[str, object],
    *,
    observed_at: datetime,
) -> ValidatedCandidate:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must include an explicit timezone")
    now = observed_at.astimezone(timezone.utc)
    schema = value.get("schema_id", value.get("schema"))
    try:
        if schema == DOOFUS_BUNDLE_SCHEMA:
            return validate_doofus_candidate(value, observed_at=now)
        if schema == WATERBOY_FORECAST_SCHEMA:
            return validate_waterboy_candidate(value, observed_at=now)
    except CandidateValidationError:
        raise
    except (TypeError, ValueError) as exc:
        raise CandidateValidationError(str(exc)) from exc
    raise CandidateValidationError(f"unsupported research candidate schema: {schema}")


@dataclass(frozen=True)
class AllocationPlan:
    plan_id: str
    candidate_digest: str
    supporting_candidate_digests: tuple[str, ...]
    mandate_id: str
    venue: Venue
    account_hash: str
    passport_digest: str
    promotion_digest: str
    authorized_instruments: tuple[str, ...]
    correlation_cluster: str
    max_order_risk_cents: int
    max_open_risk_cents: int
    max_correlated_risk_cents: int
    max_daily_loss_cents: int
    max_open_orders: int
    policy_epoch: int
    created_at: datetime
    expires_at: datetime
    body_digest: str


def parse_allocation_plan(value: Mapping[str, object]) -> AllocationPlan:
    """Parse a non-authoritative plan; strategy identity is never caller supplied."""

    expected = {
        "schema",
        "plan_id",
        "candidate_digest",
        "supporting_candidate_digests",
        "mandate_id",
        "venue",
        "account_hash",
        "passport_digest",
        "promotion_digest",
        "authorized_instruments",
        "correlation_cluster",
        "max_order_risk_cents",
        "max_open_risk_cents",
        "max_correlated_risk_cents",
        "max_daily_loss_cents",
        "max_open_orders",
        "policy_epoch",
        "created_at",
        "expires_at",
    }
    try:
        _exact_keys(value, expected, "allocation plan")
    except (TypeError, ValueError) as exc:
        raise AllocationPlanError(str(exc)) from exc
    if value["schema"] != ALLOCATION_PLAN_SCHEMA:
        raise AllocationPlanError("unsupported allocation plan schema")
    try:
        plan_id = require_identifier(
            _string(value["plan_id"], "allocation plan plan_id"),
            "allocation plan plan_id",
        )
        candidate_digest = require_digest(
            _string(
                value["candidate_digest"],
                "allocation plan candidate_digest",
            ),
            "allocation plan candidate_digest",
        )
        supporting = _string_array(
            value["supporting_candidate_digests"],
            "allocation plan supporting_candidate_digests",
        )
        require_sorted_unique(supporting, "supporting_candidate_digests")
        for digest in supporting:
            require_digest(digest, "supporting_candidate_digest")
        if candidate_digest in supporting:
            raise AllocationPlanError(
                "primary candidate cannot also be a supporting candidate"
            )
        mandate_id = require_digest(
            _string(value["mandate_id"], "allocation plan mandate_id"),
            "allocation plan mandate_id",
        )
        try:
            venue = Venue(value["venue"])
        except (TypeError, ValueError) as exc:
            raise AllocationPlanError("allocation plan venue is unsupported") from exc
        account_hash = require_digest(
            _string(value["account_hash"], "allocation plan account_hash"),
            "allocation plan account_hash",
        )
        passport_digest = require_digest(
            _string(
                value["passport_digest"],
                "allocation plan passport_digest",
            ),
            "allocation plan passport_digest",
        )
        promotion_digest = require_digest(
            _string(
                value["promotion_digest"],
                "allocation plan promotion_digest",
            ),
            "allocation plan promotion_digest",
        )
        instruments = _string_array(
            value["authorized_instruments"],
            "allocation plan authorized_instruments",
            nonempty=True,
        )
        require_sorted_unique(instruments, "authorized_instruments")
        for index, instrument in enumerate(instruments):
            validate_authorized_instrument(
                venue,
                instrument,
                f"authorized_instruments[{index}]",
            )
        correlation_cluster = require_identifier(
            _string(
                value["correlation_cluster"],
                "allocation plan correlation_cluster",
            ),
            "allocation plan correlation_cluster",
        )
        risk_values = {
            name: _integer(value[name], f"allocation plan {name}", minimum=1)
            for name in (
                "max_order_risk_cents",
                "max_open_risk_cents",
                "max_correlated_risk_cents",
                "max_daily_loss_cents",
                "max_open_orders",
                "policy_epoch",
            )
        }
        if not (
            risk_values["max_order_risk_cents"]
            <= risk_values["max_correlated_risk_cents"]
            <= risk_values["max_open_risk_cents"]
        ):
            raise AllocationPlanError(
                "allocation risk must satisfy order <= correlated <= open"
            )
        created_at = parse_utc(
            _string(value["created_at"], "allocation plan created_at"),
            "allocation plan created_at",
        )
        expires_at = parse_utc(
            _string(value["expires_at"], "allocation plan expires_at"),
            "allocation plan expires_at",
        )
        if expires_at <= created_at:
            raise AllocationPlanError(
                "allocation plan expires_at must be after created_at"
            )
        if expires_at - created_at > MAX_PLAN_WINDOW:
            raise AllocationPlanError(
                "allocation plan validity cannot exceed 24 hours"
            )
    except AllocationPlanError:
        raise
    except (TypeError, ValueError) as exc:
        raise AllocationPlanError(str(exc)) from exc
    return AllocationPlan(
        plan_id=plan_id,
        candidate_digest=candidate_digest,
        supporting_candidate_digests=supporting,
        mandate_id=mandate_id,
        venue=venue,
        account_hash=account_hash,
        passport_digest=passport_digest,
        promotion_digest=promotion_digest,
        authorized_instruments=instruments,
        correlation_cluster=correlation_cluster,
        max_order_risk_cents=risk_values["max_order_risk_cents"],
        max_open_risk_cents=risk_values["max_open_risk_cents"],
        max_correlated_risk_cents=risk_values["max_correlated_risk_cents"],
        max_daily_loss_cents=risk_values["max_daily_loss_cents"],
        max_open_orders=risk_values["max_open_orders"],
        policy_epoch=risk_values["policy_epoch"],
        created_at=created_at,
        expires_at=expires_at,
        body_digest=canonical_sha256(value),
    )


@dataclass(frozen=True)
class CoreHttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str] = MappingProxyType({})


class CoreTransport(Protocol):
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> CoreHttpResponse:
        """Perform exactly one bounded request."""


@dataclass(frozen=True)
class CoreIdentity:
    readiness_path: Path
    public_key: bytes
    release_id: str
    fund_lock_sha256: str
    service_manifest_sha256: str
    host: str
    max_readiness_bytes: int = DEFAULT_MAX_RESPONSE_BYTES

    def __post_init__(self) -> None:
        if not self.readiness_path.is_absolute():
            raise ValueError("Core readiness path must be absolute")
        if len(self.public_key) != 32:
            raise ValueError("Core public key must contain exactly 32 bytes")
        if not self.release_id.strip():
            raise ValueError("Core release_id must be non-empty")
        require_digest(self.fund_lock_sha256, "fund_lock_sha256")
        require_digest(self.service_manifest_sha256, "service_manifest_sha256")
        if self.host not in {"127.0.0.1", "::1"}:
            raise ValueError("Core host must be a literal loopback address")
        require_positive_int(self.max_readiness_bytes, "max_readiness_bytes")


class CoreReadinessVerifier:
    """Verify Core identity, endpoint, release, and current control readiness."""

    def __init__(self, identity: CoreIdentity) -> None:
        self.identity = identity
        self.keyring = Ed25519Keyring()
        self.core_key_id = self.keyring.register(identity.public_key)

    def verify(self, observed_at: datetime) -> ReadinessDescriptorV1:
        now = observed_at.astimezone(timezone.utc)
        try:
            with self.identity.readiness_path.open("rb") as handle:
                raw = handle.read(self.identity.max_readiness_bytes + 1)
        except OSError as exc:
            raise CoreReadinessError("Core readiness artifact cannot be read") from exc
        if len(raw) > self.identity.max_readiness_bytes:
            raise CoreReadinessError("Core readiness artifact exceeds its size limit")
        try:
            envelope = SignedEnvelopeV1.from_dict(
                loads_research_json(raw, "Core readiness artifact")
            )
            if envelope.source_id != CORE_SOURCE_ID:
                raise CoreReadinessError("Core readiness source is invalid")
            if envelope.signer_key_id != self.core_key_id:
                raise CoreReadinessError("Core readiness signer is not pinned")
            body = envelope.verify(self.keyring, now)
        except CoreReadinessError:
            raise
        except (TypeError, ValueError, PermissionError) as exc:
            raise CoreReadinessError("Core readiness signature is invalid or stale") from exc
        if not isinstance(body, ReadinessDescriptorV1):
            raise CoreReadinessError("Core readiness body has the wrong contract")
        if body.service_name != CORE_SOURCE_ID:
            raise CoreReadinessError("Core readiness service name is invalid")
        if body.release_id != self.identity.release_id:
            raise CoreReadinessError("Core readiness release does not match the mesh")
        if body.fund_lock_sha256 != self.identity.fund_lock_sha256:
            raise CoreReadinessError("Core readiness fund lock does not match the mesh")
        if body.service_manifest_sha256 != self.identity.service_manifest_sha256:
            raise CoreReadinessError(
                "Core readiness service manifest does not match the mesh"
            )
        endpoint = dict(body.endpoint)
        if (
            endpoint.get("transport") != "http"
            or endpoint.get("host") != self.identity.host
            or endpoint.get("base_path") != "/"
        ):
            raise CoreReadinessError("Core readiness endpoint does not match configuration")
        authority = dict(body.authority)
        if authority != {
            "broker": "NONE",
            "mode": "OFFLINE",
            "execution_enabled": False,
        }:
            raise CoreReadinessError("Core readiness widened its authority boundary")
        health = dict(body.health)
        if (
            health.get("status") != "READY"
            or health.get("control_status") != "LIVE_READY"
        ):
            raise CoreReadinessError("Core is signed but not LIVE_READY")
        if "allocator-signed-capital-requests" not in body.capabilities:
            raise CoreReadinessError("Core does not advertise allocator request intake")
        return body


@dataclass(frozen=True)
class CoreAppendReceipt:
    event_id: str
    global_sequence: int
    event_digest: str
    duplicate: bool


class AllocatorCoreClient:
    """One-shot allocator request client; it never retries a dispatched POST."""

    def __init__(
        self,
        *,
        identity: CoreIdentity,
        readiness_verifier: CoreReadinessVerifier,
        transport: CoreTransport,
        bearer_token: str,
        timeout_seconds: int = 5,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if (
            not isinstance(bearer_token, str)
            or not 32 <= len(bearer_token) <= 512
            or any(character.isspace() for character in bearer_token)
        ):
            raise ValueError(
                "allocator bearer token must be 32-512 non-whitespace characters"
            )
        require_positive_int(timeout_seconds, "timeout_seconds")
        require_positive_int(max_response_bytes, "max_response_bytes")
        self.identity = identity
        self.readiness_verifier = readiness_verifier
        self.transport = transport
        self._bearer_token = bearer_token
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    def submit(
        self,
        envelope: SignedEnvelopeV1,
        *,
        observed_at: datetime,
    ) -> CoreAppendReceipt:
        if envelope.body_schema != CapitalRequestV1.SCHEMA:
            raise TypeError("allocator client accepts CapitalRequestV1 envelopes only")
        readiness = self.readiness_verifier.verify(observed_at)
        endpoint = dict(readiness.endpoint)
        port = cast(int, endpoint["port"])
        host = f"[{self.identity.host}]" if self.identity.host == "::1" else self.identity.host
        base_url = f"http://{host}:{port}"
        payload = canonical_json_bytes(envelope.to_dict())
        try:
            response = self.transport.request(
                method="POST",
                url=f"{base_url}{ALLOCATOR_APPEND_PATH}",
                headers={
                    "authorization": f"Bearer {self._bearer_token}",
                    "content-type": "application/json",
                    "accept": "application/json",
                    "cache-control": "no-store",
                },
                body=payload,
                timeout_seconds=self.timeout_seconds,
                max_response_bytes=self.max_response_bytes,
            )
        except Exception as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise AmbiguousCoreSubmission(
                "allocator POST outcome is ambiguous and will not be retried"
            ) from exc
        if len(response.body) > self.max_response_bytes:
            raise AmbiguousCoreSubmission(
                "Core response exceeded its bound after allocator dispatch"
            )
        if response.status not in {200, 201}:
            raise ResearchMeshError(
                f"Core deterministically rejected allocator request with status {response.status}"
            )
        try:
            result = loads_research_json(
                response.body,
                "Core allocator append response",
            )
            _exact_keys(
                result,
                {
                    "schema",
                    "event_id",
                    "global_sequence",
                    "event_digest",
                    "duplicate",
                },
                "Core allocator append response",
            )
            if result["schema"] != CORE_APPEND_RESULT_SCHEMA:
                raise ValueError("Core allocator append response schema is invalid")
            event_id = require_digest(
                _string(result["event_id"], "Core response event_id"),
                "Core response event_id",
            )
            event_digest = require_digest(
                _string(result["event_digest"], "Core response event_digest"),
                "Core response event_digest",
            )
            sequence = _integer(
                result["global_sequence"],
                "Core response global_sequence",
                minimum=1,
            )
            duplicate = result["duplicate"]
            if not isinstance(duplicate, bool):
                raise TypeError("Core response duplicate must be a boolean")
            if event_id != envelope.event_id:
                raise ValueError("Core response event_id does not match the request")
            if (response.status == 200) is not duplicate:
                raise ValueError("Core response status and duplicate flag disagree")
        except (TypeError, ValueError) as exc:
            raise AmbiguousCoreSubmission(
                "Core response was invalid after allocator dispatch"
            ) from exc
        return CoreAppendReceipt(
            event_id=event_id,
            global_sequence=sequence,
            event_digest=event_digest,
            duplicate=duplicate,
        )


class ResearchMeshState:
    """Durable minimal state; candidate payloads and bearer material are absent."""

    def __init__(self, path: Path) -> None:
        if not path.is_absolute():
            raise ValueError("Research Mesh database path must be absolute")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidate_files (
                    file_sha256 TEXT PRIMARY KEY,
                    candidate_digest TEXT,
                    status TEXT NOT NULL,
                    reason_code TEXT,
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_digest TEXT PRIMARY KEY,
                    source_kind TEXT NOT NULL,
                    schema_id TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    passport_digest TEXT,
                    strategy_hash TEXT,
                    venue TEXT,
                    intended_instruments_json TEXT NOT NULL,
                    maximum_loss_cents INTEGER,
                    accepted_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS allocation_files (
                    file_sha256 TEXT PRIMARY KEY,
                    plan_id TEXT,
                    status TEXT NOT NULL,
                    reason_code TEXT,
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY,
                    body_digest TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    reason_code TEXT,
                    source_sequence INTEGER,
                    request_event_id TEXT,
                    core_event_digest TEXT,
                    core_global_sequence INTEGER,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO metadata(key, value)
                VALUES ('allocator_source_sequence', '0');
                """
            )
            # The data-root lease guarantees no live owner exists while this
            # database is opened. A persisted DISPATCHING row therefore means
            # the prior process died after the durable no-retry fence and may
            # have reached Core. It can never be replayed automatically.
            self._db.execute(
                """
                UPDATE plans
                SET state = 'AMBIGUOUS_NO_RETRY',
                    reason_code = 'RECOVERED_UNCONFIRMED_DISPATCH'
                WHERE state = 'DISPATCHING'
                """
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> "ResearchMeshState":
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        self.close()

    def has_candidate_file(self, file_sha256: str) -> bool:
        require_digest(file_sha256, "file_sha256")
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM candidate_files WHERE file_sha256 = ?",
                (file_sha256,),
            ).fetchone()
        return row is not None

    def record_candidate(
        self,
        *,
        file_sha256: str,
        candidate: ValidatedCandidate,
        observed_at: datetime,
    ) -> None:
        now = format_utc(observed_at)
        with self._lock, self._db:
            self._db.execute(
                """
                INSERT OR IGNORE INTO candidates(
                    candidate_digest, source_kind, schema_id, as_of, expires_at,
                    passport_digest, strategy_hash, venue,
                    intended_instruments_json, maximum_loss_cents, accepted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.candidate_digest,
                    candidate.source_kind,
                    candidate.schema,
                    format_utc(candidate.as_of),
                    format_utc(candidate.expires_at),
                    candidate.passport_digest,
                    candidate.strategy_hash,
                    None if candidate.venue is None else candidate.venue.value,
                    json.dumps(
                        list(candidate.intended_instruments),
                        separators=(",", ":"),
                    ),
                    candidate.maximum_loss_cents,
                    now,
                ),
            )
            self._db.execute(
                """
                INSERT OR IGNORE INTO candidate_files(
                    file_sha256, candidate_digest, status, reason_code, observed_at
                ) VALUES (?, ?, 'ACCEPTED_CANDIDATE_ONLY', NULL, ?)
                """,
                (file_sha256, candidate.candidate_digest, now),
            )

    def record_candidate_rejection(
        self,
        *,
        file_sha256: str,
        reason_code: str,
        observed_at: datetime,
    ) -> None:
        require_identifier(reason_code, "reason_code")
        with self._lock, self._db:
            self._db.execute(
                """
                INSERT OR IGNORE INTO candidate_files(
                    file_sha256, candidate_digest, status, reason_code, observed_at
                ) VALUES (?, NULL, 'REJECTED', ?, ?)
                """,
                (file_sha256, reason_code, format_utc(observed_at)),
            )

    def candidate(self, digest: str) -> ValidatedCandidate | None:
        require_digest(digest, "candidate digest")
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM candidates WHERE candidate_digest = ?",
                (digest,),
            ).fetchone()
        if row is None:
            return None
        instruments_raw = json.loads(cast(str, row["intended_instruments_json"]))
        venue_raw = row["venue"]
        return ValidatedCandidate(
            candidate_digest=cast(str, row["candidate_digest"]),
            source_kind=cast(str, row["source_kind"]),
            schema=cast(str, row["schema_id"]),
            as_of=parse_utc(cast(str, row["as_of"])),
            expires_at=parse_utc(cast(str, row["expires_at"])),
            passport_digest=cast(str | None, row["passport_digest"]),
            strategy_hash=cast(str | None, row["strategy_hash"]),
            venue=None if venue_raw is None else Venue(cast(str, venue_raw)),
            intended_instruments=tuple(cast(list[str], instruments_raw)),
            maximum_loss_cents=cast(int | None, row["maximum_loss_cents"]),
        )

    def has_allocation_file(self, file_sha256: str) -> bool:
        require_digest(file_sha256, "file_sha256")
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM allocation_files WHERE file_sha256 = ?",
                (file_sha256,),
            ).fetchone()
        return row is not None

    def record_plan(
        self,
        *,
        file_sha256: str,
        plan: AllocationPlan,
        raw_plan: Mapping[str, object],
        observed_at: datetime,
    ) -> None:
        now = format_utc(observed_at)
        plan_json = canonical_json_bytes(raw_plan).decode("utf-8")
        with self._lock, self._db:
            existing = self._db.execute(
                "SELECT body_digest FROM plans WHERE plan_id = ?",
                (plan.plan_id,),
            ).fetchone()
            if existing is not None and existing["body_digest"] != plan.body_digest:
                self._db.execute(
                    """
                    INSERT OR IGNORE INTO allocation_files(
                        file_sha256, plan_id, status, reason_code, observed_at
                    ) VALUES (?, ?, 'REJECTED', 'PLAN_ID_COLLISION', ?)
                    """,
                    (file_sha256, plan.plan_id, now),
                )
                return
            self._db.execute(
                """
                INSERT OR IGNORE INTO plans(
                    plan_id, body_digest, plan_json, state, reason_code,
                    source_sequence, request_event_id, core_event_digest,
                    core_global_sequence, updated_at
                ) VALUES (?, ?, ?, 'PENDING', NULL, NULL, NULL, NULL, NULL, ?)
                """,
                (plan.plan_id, plan.body_digest, plan_json, now),
            )
            self._db.execute(
                """
                INSERT OR IGNORE INTO allocation_files(
                    file_sha256, plan_id, status, reason_code, observed_at
                ) VALUES (?, ?, 'ACCEPTED_PLAN_ONLY', NULL, ?)
                """,
                (file_sha256, plan.plan_id, now),
            )

    def record_plan_rejection(
        self,
        *,
        file_sha256: str,
        reason_code: str,
        observed_at: datetime,
    ) -> None:
        require_identifier(reason_code, "reason_code")
        with self._lock, self._db:
            self._db.execute(
                """
                INSERT OR IGNORE INTO allocation_files(
                    file_sha256, plan_id, status, reason_code, observed_at
                ) VALUES (?, NULL, 'REJECTED', ?, ?)
                """,
                (file_sha256, reason_code, format_utc(observed_at)),
            )

    def pending_plans(self) -> tuple[tuple[AllocationPlan, Mapping[str, object]], ...]:
        with self._lock:
            rows = self._db.execute(
                """
                SELECT plan_json FROM plans
                WHERE state = 'PENDING'
                ORDER BY plan_id
                """
            ).fetchall()
        output: list[tuple[AllocationPlan, Mapping[str, object]]] = []
        for row in rows:
            raw = loads_research_json(
                cast(str, row["plan_json"]).encode("utf-8"),
                "persisted allocation plan",
            )
            output.append((parse_allocation_plan(raw), raw))
        return tuple(output)

    def reserve_dispatch(
        self,
        *,
        plan: AllocationPlan,
        envelope_factory: Callable[[int], SignedEnvelopeV1],
        observed_at: datetime,
    ) -> SignedEnvelopeV1 | None:
        """Atomically fence a plan before any externally observable POST."""

        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT state, body_digest FROM plans WHERE plan_id = ?",
                    (plan.plan_id,),
                ).fetchone()
                if (
                    row is None
                    or row["state"] != "PENDING"
                    or row["body_digest"] != plan.body_digest
                ):
                    self._db.rollback()
                    return None
                sequence_row = self._db.execute(
                    "SELECT value FROM metadata WHERE key = 'allocator_source_sequence'"
                ).fetchone()
                if sequence_row is None:
                    raise RuntimeError("allocator source sequence metadata is missing")
                sequence = int(sequence_row["value"]) + 1
                envelope = envelope_factory(sequence)
                self._db.execute(
                    """
                    UPDATE metadata SET value = ?
                    WHERE key = 'allocator_source_sequence'
                    """,
                    (str(sequence),),
                )
                self._db.execute(
                    """
                    UPDATE plans
                    SET state = 'DISPATCHING',
                        reason_code = 'POST_OUTCOME_NOT_YET_CONFIRMED',
                        source_sequence = ?,
                        request_event_id = ?,
                        updated_at = ?
                    WHERE plan_id = ?
                    """,
                    (
                        sequence,
                        envelope.event_id,
                        format_utc(observed_at),
                        plan.plan_id,
                    ),
                )
                self._db.commit()
                return envelope
            except BaseException:
                self._db.rollback()
                raise

    def finish_plan(
        self,
        *,
        plan_id: str,
        state: str,
        reason_code: str,
        observed_at: datetime,
        receipt: CoreAppendReceipt | None = None,
    ) -> None:
        if state not in {
            "CORE_ACCEPTED_REQUEST",
            "CORE_REJECTED_REQUEST",
            "AMBIGUOUS_NO_RETRY",
            "REJECTED_LOCAL",
        }:
            raise ValueError("unsupported terminal plan state")
        require_identifier(reason_code, "reason_code")
        with self._lock, self._db:
            self._db.execute(
                """
                UPDATE plans
                SET state = ?,
                    reason_code = ?,
                    core_event_digest = ?,
                    core_global_sequence = ?,
                    updated_at = ?
                WHERE plan_id = ?
                """,
                (
                    state,
                    reason_code,
                    None if receipt is None else receipt.event_digest,
                    None if receipt is None else receipt.global_sequence,
                    format_utc(observed_at),
                    plan_id,
                ),
            )

    def status_counts(self) -> Mapping[str, int]:
        with self._lock:
            candidate_rows = self._db.execute(
                "SELECT status, COUNT(*) AS count FROM candidate_files GROUP BY status"
            ).fetchall()
            plan_rows = self._db.execute(
                "SELECT state, COUNT(*) AS count FROM plans GROUP BY state"
            ).fetchall()
        counts: dict[str, int] = {}
        for row in candidate_rows:
            counts[f"candidate_files_{str(row['status']).lower()}"] = int(row["count"])
        for row in plan_rows:
            counts[f"plans_{str(row['state']).lower()}"] = int(row["count"])
        return MappingProxyType(counts)


@dataclass(frozen=True)
class CycleResult:
    candidates_accepted: int
    candidates_rejected: int
    plans_accepted: int
    plans_rejected: int
    requests_core_accepted: int
    requests_core_rejected: int
    requests_ambiguous: int
    requests_pending: int
    core_ready: bool
    observed_at: datetime


class ResearchMesh:
    """Deterministic directory intake plus singleton allocator submission loop."""

    def __init__(
        self,
        *,
        candidate_inbox: Path,
        allocation_inbox: Path,
        state: ResearchMeshState,
        allocator_signer: EnvelopeSigner,
        core_client: AllocatorCoreClient,
        clock: Callable[[], datetime] | None = None,
        max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    ) -> None:
        for path, context in (
            (candidate_inbox, "candidate_inbox"),
            (allocation_inbox, "allocation_inbox"),
        ):
            if not path.is_absolute():
                raise ValueError(f"{context} must be absolute")
            path.mkdir(parents=True, exist_ok=True)
        if candidate_inbox.resolve() == allocation_inbox.resolve():
            raise ValueError("candidate and allocation inboxes must be distinct")
        require_positive_int(max_input_bytes, "max_input_bytes")
        self.candidate_inbox = candidate_inbox.resolve()
        self.allocation_inbox = allocation_inbox.resolve()
        self.state = state
        self.allocator_signer = allocator_signer
        self.core_client = core_client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_input_bytes = max_input_bytes
        self._cycle_lock = threading.Lock()

    def _read_input(self, path: Path, root: Path) -> bytes:
        if path.is_symlink() or path.resolve().parent != root:
            raise ValueError("inbox entries must be direct non-symlink files")
        before = path.stat()
        if before.st_size <= 0 or before.st_size > self.max_input_bytes:
            raise ValueError("inbox entry size is outside the configured bound")
        with path.open("rb") as handle:
            raw = handle.read(self.max_input_bytes + 1)
        after = path.stat()
        if (
            len(raw) > self.max_input_bytes
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise ValueError("inbox entry changed while it was being read")
        return raw

    @staticmethod
    def _input_paths(root: Path) -> tuple[Path, ...]:
        return tuple(
            sorted(
                (
                    path
                    for path in root.iterdir()
                    if path.name.lower().endswith(".json")
                    and not path.name.startswith(".")
                ),
                key=lambda item: item.name,
            )
        )

    def _ingest_candidates(self, now: datetime) -> tuple[int, int]:
        accepted = 0
        rejected = 0
        for path in self._input_paths(self.candidate_inbox):
            try:
                raw = self._read_input(path, self.candidate_inbox)
            except (OSError, ValueError):
                # A changing file is retried next cycle; a stable invalid file
                # reaches the bounded validation path below.
                continue
            file_digest = hashlib.sha256(raw).hexdigest()
            if self.state.has_candidate_file(file_digest):
                continue
            try:
                value = loads_research_json(raw, "research candidate")
                candidate = validate_candidate(value, observed_at=now)
                self.state.record_candidate(
                    file_sha256=file_digest,
                    candidate=candidate,
                    observed_at=now,
                )
                accepted += 1
            except (TypeError, ValueError):
                self.state.record_candidate_rejection(
                    file_sha256=file_digest,
                    reason_code="INVALID_CANDIDATE",
                    observed_at=now,
                )
                rejected += 1
        return accepted, rejected

    def _ingest_plans(self, now: datetime) -> tuple[int, int]:
        accepted = 0
        rejected = 0
        for path in self._input_paths(self.allocation_inbox):
            try:
                raw = self._read_input(path, self.allocation_inbox)
            except (OSError, ValueError):
                continue
            file_digest = hashlib.sha256(raw).hexdigest()
            if self.state.has_allocation_file(file_digest):
                continue
            try:
                value = loads_research_json(raw, "allocation plan")
                # Capital contracts reject floats; canonical_json_bytes is an
                # explicit second-domain check before the plan is persisted.
                canonical_json_bytes(value)
                plan = parse_allocation_plan(value)
                self.state.record_plan(
                    file_sha256=file_digest,
                    plan=plan,
                    raw_plan=value,
                    observed_at=now,
                )
                accepted += 1
            except (TypeError, ValueError):
                self.state.record_plan_rejection(
                    file_sha256=file_digest,
                    reason_code="INVALID_ALLOCATION_PLAN",
                    observed_at=now,
                )
                rejected += 1
        return accepted, rejected

    def _bind_plan(
        self,
        plan: AllocationPlan,
        *,
        observed_at: datetime,
    ) -> ValidatedCandidate:
        if not plan.created_at <= observed_at < plan.expires_at:
            raise AllocationPlanError("allocation plan is not current")
        candidate = self.state.candidate(plan.candidate_digest)
        if candidate is None:
            raise LookupError("primary candidate is not yet available")
        if candidate.source_kind != "doofus":
            raise AllocationPlanError(
                "only a Doofus passport candidate can anchor allocation"
            )
        if not candidate.as_of <= observed_at < candidate.expires_at:
            raise AllocationPlanError("Doofus passport candidate is not current")
        if (
            candidate.passport_digest != plan.passport_digest
            or candidate.venue is not plan.venue
        ):
            raise AllocationPlanError(
                "allocation plan does not bind the Doofus passport and venue"
            )
        if not set(plan.authorized_instruments) <= set(
            candidate.intended_instruments
        ):
            raise AllocationPlanError(
                "allocation instruments exceed the Doofus passport candidate"
            )
        if plan.max_order_risk_cents > cast(int, candidate.maximum_loss_cents):
            raise AllocationPlanError(
                "allocation order risk exceeds the Doofus passport candidate"
            )
        for digest in plan.supporting_candidate_digests:
            supporting = self.state.candidate(digest)
            if supporting is None:
                raise LookupError("supporting candidate is not yet available")
            if supporting.source_kind != "waterboy":
                raise AllocationPlanError(
                    "supporting candidates must be Waterboy forecasts"
                )
            if not supporting.as_of <= observed_at < supporting.expires_at:
                raise AllocationPlanError(
                    "supporting Waterboy candidate is not current"
                )
        return candidate

    def _request_envelope(
        self,
        plan: AllocationPlan,
        candidate: ValidatedCandidate,
        *,
        source_sequence: int,
        observed_at: datetime,
    ) -> SignedEnvelopeV1:
        strategy_hash = cast(str, candidate.strategy_hash)
        not_before = observed_at - timedelta(seconds=1)
        expires_at = not_before + CAPITAL_REQUEST_TTL
        request = CapitalRequestV1(
            request_id=plan.plan_id,
            mandate_id=plan.mandate_id,
            venue=plan.venue,
            account_hash=plan.account_hash,
            strategy_hashes=(strategy_hash,),
            passport_hashes=(plan.passport_digest,),
            promotion_hashes=(plan.promotion_digest,),
            authorized_instruments=plan.authorized_instruments,
            correlation_cluster=plan.correlation_cluster,
            max_order_risk_cents=plan.max_order_risk_cents,
            max_open_risk_cents=plan.max_open_risk_cents,
            max_correlated_risk_cents=plan.max_correlated_risk_cents,
            max_daily_loss_cents=plan.max_daily_loss_cents,
            max_open_orders=plan.max_open_orders,
            policy_epoch=plan.policy_epoch,
            not_before=not_before,
            expires_at=expires_at,
        )
        return SignedEnvelopeV1.issue(
            request,
            source_id=ALLOCATOR_SOURCE_ID,
            source_sequence=source_sequence,
            correlation_id=plan.plan_id,
            causation_id=None,
            nonce=canonical_sha256(
                [
                    "research-mesh-allocator",
                    plan.body_digest,
                    source_sequence,
                    format_utc(observed_at),
                ]
            ),
            not_before=not_before,
            expires_at=expires_at,
            signer=self.allocator_signer,
        )

    def process_cycle(self) -> CycleResult:
        if not self._cycle_lock.acquire(blocking=False):
            raise ResearchMeshError("a Research Mesh cycle is already running")
        try:
            now = self.clock().astimezone(timezone.utc)
            candidate_accepted, candidate_rejected = self._ingest_candidates(now)
            plan_accepted, plan_rejected = self._ingest_plans(now)
            core_accepted = 0
            core_rejected = 0
            ambiguous = 0
            pending = 0
            core_ready = False
            try:
                self.core_client.readiness_verifier.verify(now)
                core_ready = True
            except CoreReadinessError:
                core_ready = False
            for plan, _raw_plan in self.state.pending_plans():
                try:
                    candidate = self._bind_plan(plan, observed_at=now)
                except LookupError:
                    pending += 1
                    continue
                except AllocationPlanError:
                    self.state.finish_plan(
                        plan_id=plan.plan_id,
                        state="REJECTED_LOCAL",
                        reason_code="LOCAL_BINDING_REJECTED",
                        observed_at=now,
                    )
                    plan_rejected += 1
                    continue
                if not core_ready:
                    pending += 1
                    continue

                def envelope_factory(source_sequence: int) -> SignedEnvelopeV1:
                    return self._request_envelope(
                        plan,
                        candidate,
                        source_sequence=source_sequence,
                        observed_at=now,
                    )

                envelope = self.state.reserve_dispatch(
                    plan=plan,
                    envelope_factory=envelope_factory,
                    observed_at=now,
                )
                if envelope is None:
                    continue
                try:
                    receipt = self.core_client.submit(
                        envelope,
                        observed_at=now,
                    )
                except AmbiguousCoreSubmission:
                    self.state.finish_plan(
                        plan_id=plan.plan_id,
                        state="AMBIGUOUS_NO_RETRY",
                        reason_code="AMBIGUOUS_POST_NO_RETRY",
                        observed_at=now,
                    )
                    ambiguous += 1
                except (CoreReadinessError, ResearchMeshError):
                    # Readiness was checked before the durable dispatch fence.
                    # Any later failure is terminal: repeating could duplicate a
                    # request whose response was lost or whose Core state moved.
                    self.state.finish_plan(
                        plan_id=plan.plan_id,
                        state="CORE_REJECTED_REQUEST",
                        reason_code="CORE_REJECTED_OR_MOVED",
                        observed_at=now,
                    )
                    core_rejected += 1
                else:
                    self.state.finish_plan(
                        plan_id=plan.plan_id,
                        state="CORE_ACCEPTED_REQUEST",
                        reason_code="REQUEST_LEDGERED_NOT_CAPITAL_GRANTED",
                        observed_at=now,
                        receipt=receipt,
                    )
                    core_accepted += 1
            return CycleResult(
                candidates_accepted=candidate_accepted,
                candidates_rejected=candidate_rejected,
                plans_accepted=plan_accepted,
                plans_rejected=plan_rejected,
                requests_core_accepted=core_accepted,
                requests_core_rejected=core_rejected,
                requests_ambiguous=ambiguous,
                requests_pending=pending,
                core_ready=core_ready,
                observed_at=now,
            )
        finally:
            self._cycle_lock.release()

    def status_snapshot(self) -> Mapping[str, object]:
        counts = dict(self.state.status_counts())
        ambiguous = counts.get("plans_ambiguous_no_retry", 0)
        dispatching = counts.get("plans_dispatching", 0)
        return MappingProxyType(
            {
                "schema": "dumbmoney.research-mesh-status.v1",
                "authority": {
                    "candidate_intake": True,
                    "evaluation": False,
                    "promotion": False,
                    "capital_grant": False,
                    "broker": False,
                    "model_provider": False,
                },
                "counts": counts,
                "blocked_by_ambiguous_submission": ambiguous + dispatching > 0,
            }
        )
