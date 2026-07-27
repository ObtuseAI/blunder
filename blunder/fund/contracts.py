"""Versioned, immutable public contracts for the DumbMoney control plane."""

from __future__ import annotations

import re
import types
import uuid
from collections.abc import Mapping as ABCMapping
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, ClassVar, Literal, Mapping, TypeVar, Union, cast, get_args, get_origin, get_type_hints

from blunder.fund.canonical import (
    canonical_json_bytes,
    canonical_sha256,
    format_utc,
    parse_utc,
    require_digest,
    require_identifier,
    require_nonnegative_int,
    require_positive_int,
    require_sorted_unique,
)
from blunder.fund.crypto import ED25519_ALGORITHM, Ed25519Keyring, EnvelopeSigner


class Venue(str, Enum):
    DUMMY_KALSHI = "dummy_kalshi"
    DOPEY_ROBINHOOD = "dopey_robinhood"


class OperatingMode(str, Enum):
    PERSONAL_PROP = "PERSONAL_PROP"
    FUND_MODE = "FUND_MODE"


class DesiredMode(str, Enum):
    READ_ONLY = "READ_ONLY"
    PAPER = "PAPER"
    LIVE = "LIVE"
    PAUSED = "PAUSED"


class PromotionStage(str, Enum):
    IDEA = "IDEA"
    REPLAY = "REPLAY"
    POINT_IN_TIME_BACKTEST = "POINT_IN_TIME_BACKTEST"
    FORWARD_SHADOW = "FORWARD_SHADOW"
    PAPER = "PAPER"
    EXPLORATORY_LIVE = "EXPLORATORY_LIVE"
    AGGRESSIVE_BOUNDED = "AGGRESSIVE_BOUNDED"


class EvidenceDecision(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class EvidenceClass(str, Enum):
    SYNTHETIC = "SYNTHETIC"
    REPLAY = "REPLAY"
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    FORWARD = "FORWARD"
    REALIZED = "REALIZED"


class ExecutionState(str, Enum):
    RESERVED = "RESERVED"
    SUBMISSION_STARTED = "SUBMISSION_STARTED"
    REVIEWED = "REVIEWED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    AMBIGUOUS = "AMBIGUOUS"
    RECONCILED = "RECONCILED"


class ReconciliationStatus(str, Enum):
    RECONCILED = "RECONCILED"
    UNRESOLVED = "UNRESOLVED"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"


def validate_authorized_instrument(venue: Venue, value: str, context: str) -> str:
    """Validate one exact venue sink identity; wildcards/classes are forbidden."""

    if not isinstance(value, str):
        raise TypeError(f"{context} must be a string")
    if venue is Venue.DUMMY_KALSHI:
        pattern = r"^event_contract:[A-Z0-9][A-Z0-9._-]{0,127}$"
    else:
        pattern = (
            r"^(?:equity:[A-Z][A-Z0-9.-]{0,31}|"
            r"option:[A-Z][A-Z0-9.-]{0,31}:[A-Za-z0-9][A-Za-z0-9._:/-]{0,127})$"
        )
    if re.fullmatch(pattern, value) is None:
        raise ValueError(f"{context} is not an exact canonical {venue.value} instrument ID")
    return value


def _freeze(value: object) -> object:
    if isinstance(value, ABCMapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _wire(value: object) -> object:
    if isinstance(value, datetime):
        return format_utc(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, CanonicalContract):
        return value.to_dict()
    if isinstance(value, ABCMapping):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire(item) for item in value]
    return value


def _decode(value: object, annotation: object, context: str) -> object:
    if annotation is Any or annotation is object:
        return _freeze(value)
    if annotation is datetime:
        if not isinstance(value, str):
            raise TypeError(f"{context} must be an RFC3339 string")
        return parse_utc(value, context)
    if annotation in {str, int, bool}:
        if annotation is int and isinstance(value, bool):
            raise TypeError(f"{context} must be an integer")
        if not isinstance(value, annotation):
            raise TypeError(f"{context} must be {annotation.__name__}")
        return value
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        try:
            return annotation(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{context} has unsupported value: {value}") from exc
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        if value not in args:
            raise ValueError(f"{context} has unsupported literal: {value}")
        return value
    if origin in {tuple, list}:
        if not isinstance(value, (list, tuple)):
            raise TypeError(f"{context} must be an array")
        item_type = args[0] if args else object
        return tuple(_decode(item, item_type, f"{context}[{index}]") for index, item in enumerate(value))
    if origin in {dict, Mapping, ABCMapping}:
        if not isinstance(value, ABCMapping):
            raise TypeError(f"{context} must be an object")
        key_type, value_type = args if len(args) == 2 else (str, object)
        if key_type is not str:
            raise TypeError(f"{context} uses an unsupported non-string key type")
        return MappingProxyType(
            {
                str(key): _decode(item, value_type, f"{context}.{key}")
                for key, item in value.items()
            }
        )
    if origin in {Union, types.UnionType}:
        if value is None and type(None) in args:
            return None
        failures: list[Exception] = []
        for option in args:
            if option is type(None):
                continue
            try:
                return _decode(value, option, context)
            except (TypeError, ValueError) as exc:
                failures.append(exc)
        raise TypeError(f"{context} does not match any supported union member") from failures[-1]
    raise TypeError(f"{context} uses unsupported contract annotation: {annotation}")


ContractT = TypeVar("ContractT", bound="CanonicalContract")


@dataclass(frozen=True)
class CanonicalContract:
    """Base for strict contracts with deterministic wire representations."""

    SCHEMA: ClassVar[str]

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, **{field.name: _wire(getattr(self, field.name)) for field in fields(self)}}

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    def digest(self) -> str:
        return canonical_sha256(self.to_dict())

    @classmethod
    def from_dict(cls: type[ContractT], value: Mapping[str, object]) -> ContractT:
        expected = {"schema", *(field.name for field in fields(cls))}
        actual = set(value)
        if actual != expected:
            raise ValueError(
                f"{cls.__name__} keys are invalid; missing={sorted(expected - actual)}; "
                f"unknown={sorted(actual - expected)}"
            )
        if value["schema"] != cls.SCHEMA:
            raise ValueError(f"{cls.__name__} schema is unsupported: {value['schema']}")
        hints = get_type_hints(cls)
        decoded = {
            field.name: _decode(value[field.name], hints[field.name], f"{cls.__name__}.{field.name}")
            for field in fields(cls)
        }
        return cls(**decoded)


def _require_window(not_before: datetime, expires_at: datetime, context: str) -> None:
    format_utc(not_before, f"{context}.not_before")
    format_utc(expires_at, f"{context}.expires_at")
    if expires_at <= not_before:
        raise ValueError(f"{context}.expires_at must be after not_before")


def _require_digest_tuple(values: tuple[str, ...], context: str, *, allow_empty: bool = False) -> None:
    require_sorted_unique(values, context)
    if not allow_empty and not values:
        raise ValueError(f"{context} must not be empty")
    for index, value in enumerate(values):
        require_digest(value, f"{context}[{index}]")


@dataclass(frozen=True)
class OperatingMandateV1(CanonicalContract):
    SCHEMA: ClassVar[str] = "dumbmoney.operating-mandate.v1"

    mandate_id: str
    operating_mode: OperatingMode
    account_hashes: Mapping[str, str]
    allowed_venues: tuple[str, ...]
    allowed_instruments: tuple[str, ...]
    nav_cents: int
    combined_capital_bps: int
    per_venue_capital_bps: Mapping[str, int]
    per_idea_loss_bps: int
    correlated_loss_bps: int
    combined_daily_loss_bps: int
    per_venue_daily_loss_bps: Mapping[str, int]
    high_water_drawdown_bps: int
    openrouter_daily_budget_cents: int
    policy_epoch: int
    not_before: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        require_digest(self.mandate_id, "mandate_id")
        if self.operating_mode is not OperatingMode.PERSONAL_PROP:
            raise ValueError("only PERSONAL_PROP is armable in DumbMoney v1")
        require_sorted_unique(self.allowed_venues, "allowed_venues")
        require_sorted_unique(self.allowed_instruments, "allowed_instruments")
        expected_venues = {venue.value for venue in Venue}
        if not self.allowed_venues or not set(self.allowed_venues) <= expected_venues:
            raise ValueError("allowed_venues contains an unsupported venue")
        supported_instruments = {"event_contract", "equity", "option"}
        if (
            not self.allowed_instruments
            or not set(self.allowed_instruments) <= supported_instruments
        ):
            raise ValueError("allowed_instruments contains an unsupported instrument type")
        if set(self.account_hashes) != set(self.allowed_venues):
            raise ValueError("account_hashes must exactly match allowed_venues")
        if set(self.per_venue_capital_bps) != set(self.allowed_venues):
            raise ValueError("per_venue_capital_bps must exactly match allowed_venues")
        if set(self.per_venue_daily_loss_bps) != set(self.allowed_venues):
            raise ValueError("per_venue_daily_loss_bps must exactly match allowed_venues")
        for venue, digest in self.account_hashes.items():
            require_digest(digest, f"account_hashes.{venue}")
        require_positive_int(self.nav_cents, "nav_cents")
        for name in (
            "combined_capital_bps",
            "per_idea_loss_bps",
            "correlated_loss_bps",
            "combined_daily_loss_bps",
            "high_water_drawdown_bps",
            "openrouter_daily_budget_cents",
        ):
            require_positive_int(cast(int, getattr(self, name)), name)
        for venue, bps in self.per_venue_capital_bps.items():
            require_positive_int(bps, f"per_venue_capital_bps.{venue}")
        for venue, bps in self.per_venue_daily_loss_bps.items():
            require_positive_int(bps, f"per_venue_daily_loss_bps.{venue}")
        require_positive_int(self.policy_epoch, "policy_epoch")
        _require_window(self.not_before, self.expires_at, "operating_mandate")


@dataclass(frozen=True)
class DesiredModeV1(CanonicalContract):
    SCHEMA: ClassVar[str] = "dumbmoney.desired-mode.v1"

    venue: Venue
    mode: DesiredMode
    revision: int
    reason: str
    policy_epoch: int
    not_before: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        require_positive_int(self.revision, "revision")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be non-empty")
        require_positive_int(self.policy_epoch, "policy_epoch")
        _require_window(self.not_before, self.expires_at, "desired_mode")


@dataclass(frozen=True)
class KillStateV1(CanonicalContract):
    SCHEMA: ClassVar[str] = "dumbmoney.kill-state.v1"

    active: bool
    generation: int
    reason: str
    changed_by: str
    policy_epoch: int
    changed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.active, bool):
            raise TypeError("active must be a boolean")
        require_positive_int(self.generation, "generation")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be non-empty")
        require_identifier(self.changed_by, "changed_by")
        require_positive_int(self.policy_epoch, "policy_epoch")
        format_utc(self.changed_at, "changed_at")


@dataclass(frozen=True)
class CapitalRequestV1(CanonicalContract):
    """Allocator-signed request; it is evidence, never capital authority."""

    SCHEMA: ClassVar[str] = "dumbmoney.capital-request.v1"

    request_id: str
    mandate_id: str
    venue: Venue
    account_hash: str
    strategy_hashes: tuple[str, ...]
    passport_hashes: tuple[str, ...]
    promotion_hashes: tuple[str, ...]
    authorized_instruments: tuple[str, ...]
    correlation_cluster: str
    max_order_risk_cents: int
    max_open_risk_cents: int
    max_correlated_risk_cents: int
    max_daily_loss_cents: int
    max_open_orders: int
    policy_epoch: int
    not_before: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.request_id, "request_id")
        require_digest(self.mandate_id, "mandate_id")
        require_digest(self.account_hash, "account_hash")
        _require_digest_tuple(self.strategy_hashes, "strategy_hashes")
        _require_digest_tuple(self.passport_hashes, "passport_hashes")
        _require_digest_tuple(self.promotion_hashes, "promotion_hashes")
        if not (
            len(self.strategy_hashes)
            == len(self.passport_hashes)
            == len(self.promotion_hashes)
        ):
            raise ValueError("strategy, passport, and promotion hash counts must match")
        if len(self.strategy_hashes) != 1:
            raise ValueError(
                "capital requests authorize exactly one strategy/passport/promotion tuple"
            )
        require_sorted_unique(self.authorized_instruments, "authorized_instruments")
        if not self.authorized_instruments:
            raise ValueError("authorized_instruments must not be empty")
        for index, instrument in enumerate(self.authorized_instruments):
            validate_authorized_instrument(
                self.venue,
                instrument,
                f"authorized_instruments[{index}]",
            )
        require_identifier(self.correlation_cluster, "correlation_cluster")
        for name in (
            "max_order_risk_cents",
            "max_open_risk_cents",
            "max_correlated_risk_cents",
            "max_daily_loss_cents",
            "max_open_orders",
            "policy_epoch",
        ):
            require_positive_int(cast(int, getattr(self, name)), name)
        if not (
            self.max_order_risk_cents
            <= self.max_correlated_risk_cents
            <= self.max_open_risk_cents
        ):
            raise ValueError(
                "capital request risk must satisfy max_order <= max_correlated <= max_open"
            )
        _require_window(self.not_before, self.expires_at, "capital_request")


@dataclass(frozen=True)
class CapitalEnvelopeV1(CanonicalContract):
    SCHEMA: ClassVar[str] = "dumbmoney.capital-envelope.v1"

    envelope_id: str
    mandate_id: str
    venue: Venue
    account_hash: str
    strategy_hashes: tuple[str, ...]
    passport_hashes: tuple[str, ...]
    promotion_hashes: tuple[str, ...]
    authorized_instruments: tuple[str, ...]
    authorized_mode: DesiredMode
    max_order_risk_cents: int
    max_open_risk_cents: int
    max_correlated_risk_cents: int
    max_daily_loss_cents: int
    max_open_orders: int
    fencing_generation: int
    policy_epoch: int
    not_before: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        require_digest(self.envelope_id, "envelope_id")
        require_digest(self.mandate_id, "mandate_id")
        require_digest(self.account_hash, "account_hash")
        _require_digest_tuple(self.strategy_hashes, "strategy_hashes")
        _require_digest_tuple(self.passport_hashes, "passport_hashes")
        _require_digest_tuple(self.promotion_hashes, "promotion_hashes")
        require_sorted_unique(self.authorized_instruments, "authorized_instruments")
        if not self.authorized_instruments:
            raise ValueError("authorized_instruments must not be empty")
        for index, instrument in enumerate(self.authorized_instruments):
            validate_authorized_instrument(self.venue, instrument, f"authorized_instruments[{index}]")
        if not (
            len(self.strategy_hashes)
            == len(self.passport_hashes)
            == len(self.promotion_hashes)
        ):
            raise ValueError("strategy, passport, and promotion hash counts must match")
        if len(self.strategy_hashes) != 1:
            raise ValueError(
                "capital envelopes authorize exactly one strategy/passport/promotion tuple"
            )
        if self.authorized_mode is not DesiredMode.LIVE:
            raise ValueError("capital envelopes can authorize LIVE mode only")
        for name in (
            "max_order_risk_cents",
            "max_open_risk_cents",
            "max_correlated_risk_cents",
            "max_daily_loss_cents",
            "max_open_orders",
            "fencing_generation",
        ):
            require_positive_int(cast(int, getattr(self, name)), name)
        if self.max_order_risk_cents > self.max_open_risk_cents:
            raise ValueError("max_order_risk_cents cannot exceed max_open_risk_cents")
        if self.max_order_risk_cents > self.max_correlated_risk_cents:
            raise ValueError("max_order_risk_cents cannot exceed max_correlated_risk_cents")
        if self.max_correlated_risk_cents > self.max_open_risk_cents:
            raise ValueError("max_correlated_risk_cents cannot exceed max_open_risk_cents")
        require_positive_int(self.policy_epoch, "policy_epoch")
        _require_window(self.not_before, self.expires_at, "capital_envelope")


@dataclass(frozen=True)
class AlphaPassportV1(CanonicalContract):
    # Doofus interop intentionally uses this underscore schema ID.
    SCHEMA: ClassVar[str] = "dumbmoney.alpha_passport.v1"

    passport_id: str
    strategy_lineage_id: str
    venue: Venue
    strategy_hash: str
    artifact_hashes: tuple[str, ...]
    evidence_verdict_hashes: tuple[str, ...]
    intended_instruments: tuple[str, ...]
    maximum_loss_cents: int
    evidence_class: EvidenceClass
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.passport_id, "passport_id")
        require_identifier(self.strategy_lineage_id, "strategy_lineage_id")
        require_digest(self.strategy_hash, "strategy_hash")
        _require_digest_tuple(self.artifact_hashes, "artifact_hashes")
        _require_digest_tuple(self.evidence_verdict_hashes, "evidence_verdict_hashes", allow_empty=True)
        require_sorted_unique(self.intended_instruments, "intended_instruments")
        if not self.intended_instruments:
            raise ValueError("intended_instruments must not be empty")
        require_positive_int(self.maximum_loss_cents, "maximum_loss_cents")
        _require_window(self.created_at, self.expires_at, "alpha_passport")


@dataclass(frozen=True)
class EvidenceVerdictV1(CanonicalContract):
    # Doofus interop intentionally uses this underscore schema ID.
    SCHEMA: ClassVar[str] = "dumbmoney.evidence_verdict.v1"

    verdict_id: str
    passport_digest: str
    evaluator_id: str
    court: Literal["integrity", "statistics", "economics", "adversarial_operations"]
    decision: EvidenceDecision
    evidence_class: EvidenceClass
    artifact_hashes: tuple[str, ...]
    reason_codes: tuple[str, ...]
    effective_trial_count: int
    evaluated_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.verdict_id, "verdict_id")
        require_digest(self.passport_digest, "passport_digest")
        require_identifier(self.evaluator_id, "evaluator_id")
        _require_digest_tuple(self.artifact_hashes, "artifact_hashes")
        require_sorted_unique(self.reason_codes, "reason_codes")
        require_nonnegative_int(self.effective_trial_count, "effective_trial_count")
        _require_window(self.evaluated_at, self.expires_at, "evidence_verdict")


@dataclass(frozen=True)
class PromotionCertificateV1(CanonicalContract):
    SCHEMA: ClassVar[str] = "dumbmoney.promotion-certificate.v1"

    certificate_id: str
    passport_digest: str
    verdict_digests: tuple[str, ...]
    stage: PromotionStage
    venue: Venue
    instruments: tuple[str, ...]
    maximum_loss_cents: int
    rollback_triggers: tuple[str, ...]
    policy_epoch: int
    not_before: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.certificate_id, "certificate_id")
        require_digest(self.passport_digest, "passport_digest")
        _require_digest_tuple(self.verdict_digests, "verdict_digests")
        require_sorted_unique(self.instruments, "instruments")
        require_sorted_unique(self.rollback_triggers, "rollback_triggers")
        if not self.instruments or not self.rollback_triggers:
            raise ValueError("instruments and rollback_triggers must not be empty")
        for index, instrument in enumerate(self.instruments):
            validate_authorized_instrument(
                self.venue,
                instrument,
                f"instruments[{index}]",
            )
        for index, trigger in enumerate(self.rollback_triggers):
            require_identifier(trigger, f"rollback_triggers[{index}]")
        require_positive_int(self.maximum_loss_cents, "maximum_loss_cents")
        require_positive_int(self.policy_epoch, "policy_epoch")
        _require_window(self.not_before, self.expires_at, "promotion_certificate")


@dataclass(frozen=True)
class ExecutionIntentV1(CanonicalContract):
    SCHEMA: ClassVar[str] = "dumbmoney.execution-intent.v1"

    intent_id: str
    idempotency_key: str
    venue: Venue
    account_hash: str
    instrument_id: str
    instrument_type: Literal["event_contract", "equity", "option"]
    authorized_instrument: str
    side: Literal["BUY", "SELL"]
    quantity: int
    limit_price_minor: int
    time_in_force: Literal["GTC", "IOC", "FOK", "DAY"]
    maximum_loss_cents: int
    correlation_cluster: str
    strategy_hash: str
    passport_digest: str
    promotion_digest: str
    capital_envelope_digest: str
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.intent_id, "intent_id")
        require_identifier(self.idempotency_key, "idempotency_key")
        require_digest(self.account_hash, "account_hash")
        if (
            not isinstance(self.instrument_id, str)
            or not self.instrument_id.strip()
            or len(self.instrument_id) > 256
        ):
            raise ValueError("instrument_id must contain 1 to 256 characters")
        validate_authorized_instrument(self.venue, self.authorized_instrument, "authorized_instrument")
        if not self.authorized_instrument.startswith(f"{self.instrument_type}:"):
            raise ValueError("authorized_instrument does not match instrument_type")
        require_positive_int(self.quantity, "quantity")
        require_positive_int(self.limit_price_minor, "limit_price_minor")
        require_positive_int(self.maximum_loss_cents, "maximum_loss_cents")
        require_identifier(self.correlation_cluster, "correlation_cluster")
        require_digest(self.strategy_hash, "strategy_hash")
        require_digest(self.passport_digest, "passport_digest")
        require_digest(self.promotion_digest, "promotion_digest")
        require_digest(self.capital_envelope_digest, "capital_envelope_digest")
        _require_window(self.created_at, self.expires_at, "execution_intent")


@dataclass(frozen=True)
class OrderLifecycleEventV1(CanonicalContract):
    SCHEMA: ClassVar[str] = "dumbmoney.order-lifecycle-event.v1"

    lifecycle_event_id: str
    intent_digest: str
    venue: Venue
    state: ExecutionState
    previous_event_digest: str | None
    broker_order_hash: str | None
    filled_quantity: int
    average_price_minor: int | None
    reason_codes: tuple[str, ...]
    broker_event_at: datetime | None
    received_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.lifecycle_event_id, "lifecycle_event_id")
        require_digest(self.intent_digest, "intent_digest")
        if self.previous_event_digest is not None:
            require_digest(self.previous_event_digest, "previous_event_digest")
        if self.broker_order_hash is not None:
            require_digest(self.broker_order_hash, "broker_order_hash")
        require_nonnegative_int(self.filled_quantity, "filled_quantity")
        if self.average_price_minor is not None:
            require_positive_int(self.average_price_minor, "average_price_minor")
        require_sorted_unique(self.reason_codes, "reason_codes")
        if self.broker_event_at is not None:
            format_utc(self.broker_event_at, "broker_event_at")
        format_utc(self.received_at, "received_at")


@dataclass(frozen=True)
class OutcomeSettlementV1(CanonicalContract):
    SCHEMA: ClassVar[str] = "dumbmoney.outcome-settlement.v1"

    settlement_id: str
    intent_digest: str
    lifecycle_digest: str
    venue: Venue
    gross_pnl_cents: int
    fees_cents: int
    slippage_cents: int
    model_cost_cents: int
    net_pnl_cents: int
    reconciliation_status: ReconciliationStatus
    settled_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.settlement_id, "settlement_id")
        require_digest(self.intent_digest, "intent_digest")
        require_digest(self.lifecycle_digest, "lifecycle_digest")
        for name in ("gross_pnl_cents", "fees_cents", "slippage_cents", "model_cost_cents", "net_pnl_cents"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
        if self.fees_cents < 0 or self.slippage_cents < 0 or self.model_cost_cents < 0:
            raise ValueError("fees, slippage, and model cost cannot be negative")
        expected_net = self.gross_pnl_cents - self.fees_cents - self.slippage_cents - self.model_cost_cents
        if self.net_pnl_cents != expected_net:
            raise ValueError("net_pnl_cents does not reconcile to gross less all declared costs")
        format_utc(self.settled_at, "settled_at")


@dataclass(frozen=True)
class VenueRiskSnapshotV1(CanonicalContract):
    SCHEMA: ClassVar[str] = "dumbmoney.venue-risk-snapshot.v1"

    snapshot_id: str
    venue: Venue
    account_hash: str
    reconciliation_receipt_digest: str
    broker_snapshot_digest: str
    nav_cents: int
    open_risk_cents: int
    daily_loss_cents: int
    high_water_drawdown_cents: int
    open_orders: int
    open_positions: int
    correlated_open_risk_cents: Mapping[str, int]
    observed_at: datetime

    def __post_init__(self) -> None:
        require_digest(self.snapshot_id, "snapshot_id")
        require_digest(self.account_hash, "account_hash")
        require_digest(self.reconciliation_receipt_digest, "reconciliation_receipt_digest")
        require_digest(self.broker_snapshot_digest, "broker_snapshot_digest")
        require_positive_int(self.nav_cents, "nav_cents")
        for name in (
            "open_risk_cents",
            "daily_loss_cents",
            "high_water_drawdown_cents",
            "open_orders",
            "open_positions",
        ):
            require_nonnegative_int(cast(int, getattr(self, name)), name)
        correlated = dict(self.correlated_open_risk_cents)
        for cluster, risk in correlated.items():
            require_identifier(cluster, f"correlated_open_risk_cents key {cluster!r}")
            require_nonnegative_int(risk, f"correlated_open_risk_cents.{cluster}")
        if self.open_risk_cents > 0 and sum(correlated.values()) < self.open_risk_cents:
            raise ValueError("correlated risk attribution must cover all open risk")
        if self.open_positions > 0 and self.open_risk_cents == 0:
            raise ValueError("open positions require a witnessed non-zero open-risk amount")
        object.__setattr__(self, "correlated_open_risk_cents", MappingProxyType(correlated))
        format_utc(self.observed_at, "observed_at")


@dataclass(frozen=True)
class CellJournalHeadAnchorV1(CanonicalContract):
    """Core-signed monotonic checkpoint for one venue-local state store."""

    SCHEMA: ClassVar[str] = "dumbmoney.cell-journal-head-anchor.v1"

    anchor_id: str
    venue: Venue
    account_hash: str
    journal_name: str
    journal_schema: str
    journal_stream_id: str
    journal_sequence: int
    journal_head_sha256: str
    previous_anchor_body_digest: str
    anchored_at: datetime

    def __post_init__(self) -> None:
        require_digest(self.anchor_id, "anchor_id")
        require_digest(self.account_hash, "account_hash")
        require_identifier(self.journal_name, "journal_name")
        require_identifier(self.journal_schema, "journal_schema")
        require_digest(self.journal_stream_id, "journal_stream_id")
        require_nonnegative_int(self.journal_sequence, "journal_sequence")
        require_digest(self.journal_head_sha256, "journal_head_sha256")
        if (self.journal_sequence == 0) != (
            self.journal_head_sha256 == "0" * 64
        ):
            raise ValueError(
                "journal sequence zero must exactly bind the zero head"
            )
        require_digest(
            self.previous_anchor_body_digest,
            "previous_anchor_body_digest",
        )
        format_utc(self.anchored_at, "anchored_at")
        expected_anchor_id = canonical_sha256(
            {
                "schema": self.SCHEMA,
                "venue": self.venue.value,
                "account_hash": self.account_hash,
                "journal_name": self.journal_name,
                "journal_schema": self.journal_schema,
                "journal_stream_id": self.journal_stream_id,
                "journal_sequence": self.journal_sequence,
                "journal_head_sha256": self.journal_head_sha256,
                "previous_anchor_body_digest": (
                    self.previous_anchor_body_digest
                ),
            }
        )
        if self.anchor_id != expected_anchor_id:
            raise ValueError(
                "anchor_id does not match the canonical journal head identity"
            )


@dataclass(frozen=True)
class MigrationManifestV1(CanonicalContract):
    SCHEMA: ClassVar[str] = "dumbmoney.migration-manifest.v1"

    migration_id: str
    source_machine_hash: str
    source_repository_hash: str
    schema_versions: tuple[str, ...]
    file_hashes: Mapping[str, str]
    row_counts: Mapping[str, int]
    contains_secrets: bool
    created_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.migration_id, "migration_id")
        require_digest(self.source_machine_hash, "source_machine_hash")
        require_digest(self.source_repository_hash, "source_repository_hash")
        require_sorted_unique(self.schema_versions, "schema_versions")
        if not self.schema_versions or not self.file_hashes:
            raise ValueError("schema_versions and file_hashes must not be empty")
        for path, digest in self.file_hashes.items():
            if not path or path.startswith(("/", "\\")) or ".." in path.replace("\\", "/").split("/"):
                raise ValueError(f"file_hashes contains unsafe relative path: {path}")
            require_digest(digest, f"file_hashes.{path}")
        for name, count in self.row_counts.items():
            if not name:
                raise ValueError("row_counts keys must be non-empty")
            require_nonnegative_int(count, f"row_counts.{name}")
        if self.contains_secrets:
            raise ValueError("migration manifests containing secrets are forbidden")
        format_utc(self.created_at, "created_at")


@dataclass(frozen=True)
class MigrationReceiptV1(CanonicalContract):
    SCHEMA: ClassVar[str] = "dumbmoney.migration-receipt.v1"

    receipt_id: str
    manifest_digest: str
    imported_count: int
    skipped_count: int
    quarantined_count: int
    reconciliation_digest: str | None
    completed_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.receipt_id, "receipt_id")
        require_digest(self.manifest_digest, "manifest_digest")
        for name in ("imported_count", "skipped_count", "quarantined_count"):
            require_nonnegative_int(cast(int, getattr(self, name)), name)
        if self.reconciliation_digest is not None:
            require_digest(self.reconciliation_digest, "reconciliation_digest")
        format_utc(self.completed_at, "completed_at")


@dataclass(frozen=True)
class InitialReconciliationReceiptV1(CanonicalContract):
    SCHEMA: ClassVar[str] = "dumbmoney.initial-reconciliation-receipt.v1"

    receipt_id: str
    venue: Venue
    account_hash: str
    broker_snapshot_digest: str
    open_orders: int
    open_positions: int
    unknown_outcomes: int
    status: ReconciliationStatus
    observed_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.receipt_id, "receipt_id")
        require_digest(self.account_hash, "account_hash")
        require_digest(self.broker_snapshot_digest, "broker_snapshot_digest")
        for name in ("open_orders", "open_positions", "unknown_outcomes"):
            require_nonnegative_int(cast(int, getattr(self, name)), name)
        if self.status is ReconciliationStatus.RECONCILED and self.unknown_outcomes:
            raise ValueError("RECONCILED receipt cannot contain unknown outcomes")
        format_utc(self.observed_at, "observed_at")


@dataclass(frozen=True)
class InheritedExposureReceiptV1(CanonicalContract):
    SCHEMA: ClassVar[str] = "dumbmoney.inherited-exposure-receipt.v1"

    receipt_id: str
    reconciliation_receipt_digest: str
    venue: Venue
    account_hash: str
    broker_exposure_hashes: tuple[str, ...]
    adopted_worst_case_loss_cents: int
    accepted_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.receipt_id, "receipt_id")
        require_digest(self.reconciliation_receipt_digest, "reconciliation_receipt_digest")
        require_digest(self.account_hash, "account_hash")
        _require_digest_tuple(self.broker_exposure_hashes, "broker_exposure_hashes", allow_empty=True)
        require_nonnegative_int(self.adopted_worst_case_loss_cents, "adopted_worst_case_loss_cents")
        if bool(self.broker_exposure_hashes) != bool(self.adopted_worst_case_loss_cents):
            raise ValueError("adopted exposure hashes and worst-case loss must both be zero or both be non-zero")
        format_utc(self.accepted_at, "accepted_at")


@dataclass(frozen=True)
class DeploymentReceiptV1(CanonicalContract):
    SCHEMA: ClassVar[str] = "dumbmoney.deployment-receipt.v1"

    deployment_id: str
    release_manifest_digest: str
    core_runner_config_sha256: str
    fund_lock_sha256: str
    venue: Venue
    reconciliation_receipt_digest: str
    place_cancel_proof_digest: str
    restart_proof_digest: str
    kill_proof_digest: str
    restore_proof_digest: str
    completed_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.deployment_id, "deployment_id")
        for name in (
            "release_manifest_digest",
            "core_runner_config_sha256",
            "fund_lock_sha256",
            "reconciliation_receipt_digest",
            "place_cancel_proof_digest",
            "restart_proof_digest",
            "kill_proof_digest",
            "restore_proof_digest",
        ):
            require_digest(cast(str, getattr(self, name)), name)
        format_utc(self.completed_at, "completed_at")


@dataclass(frozen=True)
class ReadinessDescriptorV1(CanonicalContract):
    """Signed, short-lived service discovery without bearer material."""

    SCHEMA: ClassVar[str] = "dumbmoney.readiness-descriptor.v1"

    service_name: Literal[
        "DumbMoneyCore",
        "DumbMoneyResearchMesh",
        "DumbMoneyModelGateway",
        "DumbMoneyDummyKalshi",
        "DumbMoneyDopeyRobinhood",
    ]
    release_id: str
    instance_id: str
    process_id: int
    generation: int
    observed_at: datetime
    valid_until: datetime
    endpoint: Mapping[str, object]
    fund_lock_sha256: str
    service_manifest_sha256: str
    authority: Mapping[str, object]
    health: Mapping[str, object]
    capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.release_id, str) or not self.release_id.strip():
            raise ValueError("release_id must be non-empty")
        try:
            parsed_instance = uuid.UUID(self.instance_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("instance_id must be a canonical UUID") from exc
        if str(parsed_instance) != self.instance_id:
            raise ValueError("instance_id must be a canonical UUID")
        require_positive_int(self.process_id, "process_id")
        require_nonnegative_int(self.generation, "generation")
        _require_window(self.observed_at, self.valid_until, "readiness")
        if self.valid_until - self.observed_at > timedelta(seconds=120):
            raise ValueError("readiness validity cannot exceed 120 seconds")
        endpoint = dict(self.endpoint)
        if set(endpoint) != {"transport", "host", "port", "base_path"}:
            raise ValueError("readiness endpoint fields are invalid")
        if endpoint["transport"] != "http" or endpoint["host"] not in {
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("readiness endpoint must use literal loopback HTTP")
        port = endpoint["port"]
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1024 <= port <= 65_535
        ):
            raise ValueError("readiness endpoint port must be from 1024 through 65535")
        if endpoint["base_path"] != "/":
            raise ValueError("readiness endpoint base_path must be /")
        require_digest(self.fund_lock_sha256, "fund_lock_sha256")
        require_digest(self.service_manifest_sha256, "service_manifest_sha256")
        authority = dict(self.authority)
        if set(authority) != {"broker", "mode", "execution_enabled"}:
            raise ValueError("readiness authority fields are invalid")
        if authority["broker"] not in {"NONE", "KALSHI", "ROBINHOOD"}:
            raise ValueError("readiness broker authority is invalid")
        if authority["mode"] not in {
            "OFFLINE",
            "RECONCILIATION_ONLY",
            "MECHANICAL_CANARY",
            "AGGRESSIVE_BOUNDED",
        }:
            raise ValueError("readiness authority mode is invalid")
        if not isinstance(authority["execution_enabled"], bool):
            raise TypeError("readiness execution_enabled must be a boolean")
        if authority["broker"] == "NONE" and authority["execution_enabled"]:
            raise ValueError("non-venue readiness cannot declare execution authority")
        health = dict(self.health)
        if health.get("status") not in {"READY", "DEGRADED", "BLOCKED"}:
            raise ValueError("readiness health status is invalid")
        require_sorted_unique(self.capabilities, "capabilities")
        object.__setattr__(self, "endpoint", MappingProxyType(endpoint))
        object.__setattr__(self, "authority", MappingProxyType(authority))
        object.__setattr__(
            self,
            "health",
            cast(Mapping[str, object], _freeze(health)),
        )


SUPPORTED_CONTRACTS: Mapping[str, type[CanonicalContract]] = MappingProxyType(
    {
        contract.SCHEMA: contract
        for contract in (
            OperatingMandateV1,
            DesiredModeV1,
            KillStateV1,
            CapitalRequestV1,
            CapitalEnvelopeV1,
            AlphaPassportV1,
            EvidenceVerdictV1,
            PromotionCertificateV1,
            ExecutionIntentV1,
            OrderLifecycleEventV1,
            OutcomeSettlementV1,
            VenueRiskSnapshotV1,
            CellJournalHeadAnchorV1,
            MigrationManifestV1,
            MigrationReceiptV1,
            InitialReconciliationReceiptV1,
            InheritedExposureReceiptV1,
            DeploymentReceiptV1,
            ReadinessDescriptorV1,
        )
    }
)


def parse_contract(value: Mapping[str, object]) -> CanonicalContract:
    schema = value.get("schema")
    if not isinstance(schema, str):
        raise TypeError("contract schema must be a string")
    contract_type = SUPPORTED_CONTRACTS.get(schema)
    if contract_type is None:
        raise ValueError(f"unsupported DumbMoney contract schema: {schema}")
    return contract_type.from_dict(value)


@dataclass(frozen=True)
class SignedEnvelopeV1(CanonicalContract):
    SCHEMA: ClassVar[str] = "dumbmoney.signed-envelope.v1"

    source_id: str
    source_sequence: int
    event_id: str
    correlation_id: str
    causation_id: str | None
    nonce: str
    not_before: datetime
    expires_at: datetime
    body_schema: str
    body_digest: str
    body: Mapping[str, object]
    signature_algorithm: str
    signer_key_id: str
    signature: str

    def __post_init__(self) -> None:
        require_identifier(self.source_id, "source_id")
        require_positive_int(self.source_sequence, "source_sequence")
        require_digest(self.event_id, "event_id")
        require_identifier(self.correlation_id, "correlation_id")
        if self.causation_id is not None:
            require_digest(self.causation_id, "causation_id")
        require_identifier(self.nonce, "nonce")
        _require_window(self.not_before, self.expires_at, "signed_envelope")
        require_identifier(self.body_schema, "body_schema")
        require_digest(self.body_digest, "body_digest")
        if not isinstance(self.body, ABCMapping):
            raise TypeError("body must be an object")
        if self.signature_algorithm != ED25519_ALGORITHM:
            raise ValueError(f"unsupported signature algorithm: {self.signature_algorithm}")
        require_digest(self.signer_key_id, "signer_key_id")
        if not isinstance(self.signature, str) or not self.signature:
            raise ValueError("signature must be non-empty")

    def unsigned_dict(self) -> dict[str, object]:
        value = self.to_dict()
        value.pop("signature")
        return value

    def event_identity_dict(self) -> dict[str, object]:
        value = self.unsigned_dict()
        value.pop("event_id")
        return value

    def signing_bytes(self) -> bytes:
        return canonical_json_bytes(self.unsigned_dict())

    def is_valid_at(self, observed_at: datetime) -> bool:
        """Return temporal authority without conflating it with authenticity."""

        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must include an explicit timezone")
        now = observed_at.astimezone(timezone.utc)
        return self.not_before <= now < self.expires_at

    def verify_authenticity(self, keyring: Ed25519Keyring) -> CanonicalContract:
        """Verify immutable identity/signature even for historical envelopes.

        Consumers must separately call :meth:`is_valid_at` before activating a
        positive authority grant. Historical kill/pause events can still be
        authenticated and applied monotonically.
        """

        observed_schema = self.body.get("schema")
        if observed_schema != self.body_schema:
            raise ValueError("signed envelope body_schema does not match embedded body")
        observed_body_digest = canonical_sha256(self.body)
        if observed_body_digest != self.body_digest:
            raise ValueError("signed envelope body digest mismatch")
        observed_event_id = canonical_sha256(self.event_identity_dict())
        if observed_event_id != self.event_id:
            raise ValueError("signed envelope event ID mismatch")
        keyring.verify(
            self.signer_key_id,
            self.signature_algorithm,
            self.signing_bytes(),
            self.signature,
        )
        return parse_contract(self.body)

    def verify(self, keyring: Ed25519Keyring, observed_at: datetime) -> CanonicalContract:
        if not self.is_valid_at(observed_at):
            raise PermissionError("signed envelope is not currently valid")
        return self.verify_authenticity(keyring)

    @classmethod
    def issue(
        cls,
        body: CanonicalContract,
        *,
        source_id: str,
        source_sequence: int,
        correlation_id: str,
        causation_id: str | None,
        nonce: str,
        not_before: datetime,
        expires_at: datetime,
        signer: EnvelopeSigner,
    ) -> "SignedEnvelopeV1":
        body_value = body.to_dict()
        common: dict[str, object] = {
            "schema": cls.SCHEMA,
            "source_id": source_id,
            "source_sequence": source_sequence,
            "correlation_id": correlation_id,
            "causation_id": causation_id,
            "nonce": nonce,
            "not_before": format_utc(not_before, "not_before"),
            "expires_at": format_utc(expires_at, "expires_at"),
            "body_schema": body.SCHEMA,
            "body_digest": canonical_sha256(body_value),
            "body": body_value,
            "signature_algorithm": signer.algorithm,
            "signer_key_id": signer.key_id,
        }
        event_id = canonical_sha256(common)
        unsigned = {**common, "event_id": event_id}
        # Reorder is immaterial to canonical JSON but from_dict enforces the wire domain.
        signature = signer.sign(canonical_json_bytes(unsigned))
        return cls.from_dict({**unsigned, "signature": signature})


SUPPORTED_WIRE_SCHEMAS: Mapping[str, type[CanonicalContract]] = MappingProxyType(
    {**SUPPORTED_CONTRACTS, SignedEnvelopeV1.SCHEMA: SignedEnvelopeV1}
)
