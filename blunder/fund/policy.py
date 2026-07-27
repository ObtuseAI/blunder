"""Deterministic DumbMoney risk policy and capital-envelope calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, cast

from blunder.fund.canonical import (
    canonical_sha256,
    load_strict_json,
    require_digest,
    require_identifier,
    require_nonnegative_int,
    require_positive_int,
    require_sorted_unique,
)
from blunder.fund.contracts import (
    DesiredMode,
    OperatingMandateV1,
    Venue,
    validate_authorized_instrument,
)


class PolicyViolation(PermissionError):
    """Raised when an authority request exceeds the owner-signed policy."""


MAX_FUTURE_SKEW = timedelta(seconds=5)


@dataclass(frozen=True)
class RiskPolicyV1:
    schema: str
    policy_epoch: int
    combined_capital_bps: int
    per_venue_capital_bps: Mapping[str, int]
    per_idea_loss_bps: int
    correlated_loss_bps: int
    combined_daily_loss_bps: int
    per_venue_daily_loss_bps: Mapping[str, int]
    high_water_drawdown_bps: int
    openrouter_daily_budget_cents: int
    capital_envelope_ttl_seconds: int
    mandate_max_ttl_days: int
    max_open_orders_per_venue: int
    max_positions_per_venue: int
    digest: str

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RiskPolicyV1":
        expected = {
            "schema",
            "policy_epoch",
            "combined_capital_bps",
            "per_venue_capital_bps",
            "per_idea_loss_bps",
            "correlated_loss_bps",
            "combined_daily_loss_bps",
            "per_venue_daily_loss_bps",
            "high_water_drawdown_bps",
            "openrouter_daily_budget_cents",
            "capital_envelope_ttl_seconds",
            "mandate_max_ttl_days",
            "max_open_orders_per_venue",
            "max_positions_per_venue",
        }
        if set(value) != expected:
            raise ValueError(
                f"risk policy keys are invalid; missing={sorted(expected - set(value))}; "
                f"unknown={sorted(set(value) - expected)}"
            )
        if value["schema"] != "dumbmoney.risk-policy.v1":
            raise ValueError(f"unsupported risk policy schema: {value['schema']}")
        epoch = value["policy_epoch"]
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch <= 0:
            raise ValueError("policy_epoch must be a positive integer")
        raw_venues = value["per_venue_capital_bps"]
        if not isinstance(raw_venues, dict) or set(raw_venues) != {venue.value for venue in Venue}:
            raise ValueError("per_venue_capital_bps must define exactly both canonical venues")
        venue_limits: dict[str, int] = {}
        for venue, raw_limit in raw_venues.items():
            if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
                raise TypeError(f"per_venue_capital_bps.{venue} must be an integer")
            venue_limits[venue] = require_positive_int(raw_limit, f"per_venue_capital_bps.{venue}")
        raw_venue_daily = value["per_venue_daily_loss_bps"]
        if not isinstance(raw_venue_daily, dict) or set(raw_venue_daily) != {venue.value for venue in Venue}:
            raise ValueError("per_venue_daily_loss_bps must define exactly both canonical venues")
        venue_daily_limits: dict[str, int] = {}
        for venue, raw_limit in raw_venue_daily.items():
            if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
                raise TypeError(f"per_venue_daily_loss_bps.{venue} must be an integer")
            venue_daily_limits[venue] = require_positive_int(raw_limit, f"per_venue_daily_loss_bps.{venue}")
        integer_names = expected - {
            "schema",
            "policy_epoch",
            "per_venue_capital_bps",
            "per_venue_daily_loss_bps",
        }
        parsed: dict[str, int] = {}
        for name in integer_names:
            raw = value[name]
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise TypeError(f"{name} must be an integer")
            parsed[name] = require_positive_int(raw, name)
        if parsed["combined_capital_bps"] > 10_000:
            raise ValueError("combined_capital_bps cannot exceed 100%")
        if sum(venue_limits.values()) > parsed["combined_capital_bps"]:
            raise ValueError("sum of per-venue limits cannot exceed the combined capital limit")
        if parsed["per_idea_loss_bps"] > min(venue_limits.values()):
            raise ValueError("per-idea loss limit cannot exceed a venue capital limit")
        if parsed["correlated_loss_bps"] > parsed["combined_capital_bps"]:
            raise ValueError("correlated loss limit cannot exceed combined capital limit")
        return cls(
            schema=value["schema"],
            policy_epoch=epoch,
            per_venue_capital_bps=MappingProxyType(venue_limits),
            per_venue_daily_loss_bps=MappingProxyType(venue_daily_limits),
            digest=canonical_sha256(value),
            **parsed,
        )

    @classmethod
    def load(cls, path: Path) -> "RiskPolicyV1":
        return cls.from_dict(load_strict_json(path))


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Broker-reconciled risk state used to decide a capital lease."""

    nav_cents: int
    combined_open_risk_cents: int
    venue_open_risk_cents: Mapping[str, int]
    correlated_open_risk_cents: Mapping[str, int]
    combined_daily_loss_cents: int
    venue_daily_loss_cents: Mapping[str, int]
    high_water_drawdown_cents: int
    venue_open_orders: Mapping[str, int]
    venue_open_positions: Mapping[str, int]
    reconciled_venues: tuple[str, ...]
    observed_at: datetime

    def __post_init__(self) -> None:
        require_positive_int(self.nav_cents, "snapshot.nav_cents")
        for name in (
            "combined_open_risk_cents",
            "combined_daily_loss_cents",
            "high_water_drawdown_cents",
        ):
            require_nonnegative_int(cast(int, getattr(self, name)), f"snapshot.{name}")
        expected = {venue.value for venue in Venue}
        if (
            set(self.venue_open_risk_cents) != expected
            or set(self.venue_open_orders) != expected
            or set(self.venue_daily_loss_cents) != expected
            or set(self.venue_open_positions) != expected
        ):
            raise ValueError("snapshot must include risk, daily loss, and order counts for both venues")
        for mapping_name in (
            "venue_open_risk_cents",
            "correlated_open_risk_cents",
            "venue_open_orders",
            "venue_open_positions",
            "venue_daily_loss_cents",
        ):
            for key, amount in getattr(self, mapping_name).items():
                if not isinstance(key, str) or not key:
                    raise ValueError(f"snapshot.{mapping_name} keys must be non-empty")
                require_nonnegative_int(amount, f"snapshot.{mapping_name}.{key}")
        if tuple(sorted(set(self.reconciled_venues))) != self.reconciled_venues:
            raise ValueError("snapshot.reconciled_venues must be sorted and unique")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("snapshot.observed_at must include an explicit timezone")
        if sum(self.venue_open_risk_cents.values()) != self.combined_open_risk_cents:
            raise ValueError("combined_open_risk_cents must equal the sum of venue risk")
        for name in (
            "venue_open_risk_cents",
            "correlated_open_risk_cents",
            "venue_daily_loss_cents",
            "venue_open_orders",
            "venue_open_positions",
        ):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))


@dataclass(frozen=True)
class CapitalRequest:
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

    def __post_init__(self) -> None:
        require_digest(self.account_hash, "request.account_hash")
        for name in ("strategy_hashes", "passport_hashes", "promotion_hashes"):
            values = cast(tuple[str, ...], getattr(self, name))
            require_sorted_unique(values, f"request.{name}")
            if not values:
                raise ValueError(f"request.{name} must not be empty")
            for index, digest in enumerate(values):
                require_digest(digest, f"request.{name}[{index}]")
        if not (
            len(self.strategy_hashes)
            == len(self.passport_hashes)
            == len(self.promotion_hashes)
        ):
            raise ValueError("request strategy, passport, and promotion hash counts must match")
        if len(self.strategy_hashes) != 1:
            raise ValueError(
                "requests authorize exactly one strategy/passport/promotion tuple"
            )
        require_sorted_unique(self.authorized_instruments, "request.authorized_instruments")
        if not self.authorized_instruments:
            raise ValueError("request.authorized_instruments must not be empty")
        for index, instrument in enumerate(self.authorized_instruments):
            validate_authorized_instrument(
                self.venue,
                instrument,
                f"request.authorized_instruments[{index}]",
            )
        require_identifier(self.correlation_cluster, "request.correlation_cluster")
        for name in (
            "max_order_risk_cents",
            "max_open_risk_cents",
            "max_correlated_risk_cents",
            "max_daily_loss_cents",
            "max_open_orders",
        ):
            require_positive_int(cast(int, getattr(self, name)), f"request.{name}")
        if not (
            self.max_order_risk_cents
            <= self.max_correlated_risk_cents
            <= self.max_open_risk_cents
        ):
            raise ValueError(
                "request risk must satisfy max_order <= max_correlated <= max_open"
            )


@dataclass(frozen=True)
class CapitalDecision:
    allowed: bool
    reason_codes: tuple[str, ...]
    effective_nav_cents: int
    combined_limit_cents: int
    venue_limit_cents: int
    idea_limit_cents: int
    correlated_limit_cents: int
    daily_loss_limit_cents: int
    venue_daily_loss_limit_cents: int
    drawdown_limit_cents: int

    def require_allowed(self) -> None:
        if not self.allowed:
            raise PolicyViolation(f"capital request denied: {','.join(self.reason_codes)}")


class RiskPolicyEngine:
    """Pure, denial-first policy evaluator.  It has no broker or model access."""

    def __init__(self, policy: RiskPolicyV1) -> None:
        self.policy = policy

    @staticmethod
    def _basis_points(amount_cents: int, basis_points: int) -> int:
        return amount_cents * basis_points // 10_000

    def validate_mandate(self, mandate: OperatingMandateV1, observed_at: datetime) -> None:
        now = observed_at.astimezone(timezone.utc)
        reasons: list[str] = []
        if mandate.policy_epoch != self.policy.policy_epoch:
            reasons.append("POLICY_EPOCH_MISMATCH")
        if not mandate.not_before <= now < mandate.expires_at:
            reasons.append("MANDATE_NOT_CURRENT")
        if mandate.expires_at - mandate.not_before > timedelta(days=self.policy.mandate_max_ttl_days):
            reasons.append("MANDATE_TTL_EXCEEDS_POLICY")
        if mandate.combined_capital_bps > self.policy.combined_capital_bps:
            reasons.append("COMBINED_CAPITAL_EXCEEDS_POLICY")
        if mandate.per_idea_loss_bps > self.policy.per_idea_loss_bps:
            reasons.append("IDEA_LOSS_EXCEEDS_POLICY")
        if mandate.correlated_loss_bps > self.policy.correlated_loss_bps:
            reasons.append("CORRELATED_LOSS_EXCEEDS_POLICY")
        if mandate.combined_daily_loss_bps > self.policy.combined_daily_loss_bps:
            reasons.append("DAILY_LOSS_EXCEEDS_POLICY")
        for venue, limit in mandate.per_venue_daily_loss_bps.items():
            if limit > self.policy.per_venue_daily_loss_bps[venue]:
                reasons.append(f"VENUE_DAILY_LOSS_EXCEEDS_POLICY:{venue}")
        if mandate.high_water_drawdown_bps > self.policy.high_water_drawdown_bps:
            reasons.append("DRAWDOWN_EXCEEDS_POLICY")
        if mandate.openrouter_daily_budget_cents != self.policy.openrouter_daily_budget_cents:
            reasons.append("MODEL_BUDGET_MUST_EQUAL_POLICY")
        for venue, limit in mandate.per_venue_capital_bps.items():
            if limit > self.policy.per_venue_capital_bps[venue]:
                reasons.append(f"VENUE_CAPITAL_EXCEEDS_POLICY:{venue}")
        if sum(mandate.per_venue_capital_bps.values()) > mandate.combined_capital_bps:
            reasons.append("VENUE_CAPITAL_SUM_EXCEEDS_MANDATE")
        if reasons:
            raise PolicyViolation(f"operating mandate denied: {','.join(sorted(reasons))}")

    def evaluate(
        self,
        mandate: OperatingMandateV1,
        request: CapitalRequest,
        snapshot: PortfolioSnapshot,
        observed_at: datetime,
        *,
        kill_active: bool,
        desired_mode: DesiredMode,
    ) -> CapitalDecision:
        self.validate_mandate(mandate, observed_at)
        effective_nav = min(mandate.nav_cents, snapshot.nav_cents)
        combined_limit = self._basis_points(effective_nav, mandate.combined_capital_bps)
        venue_limit = self._basis_points(effective_nav, mandate.per_venue_capital_bps.get(request.venue.value, 0))
        idea_limit = self._basis_points(effective_nav, mandate.per_idea_loss_bps)
        correlated_limit = self._basis_points(effective_nav, mandate.correlated_loss_bps)
        daily_limit = self._basis_points(effective_nav, mandate.combined_daily_loss_bps)
        venue_daily_limit = self._basis_points(
            effective_nav,
            mandate.per_venue_daily_loss_bps[request.venue.value],
        )
        drawdown_limit = self._basis_points(effective_nav, mandate.high_water_drawdown_bps)
        reasons: list[str] = []
        if kill_active:
            reasons.append("GLOBAL_KILL_ACTIVE")
        if desired_mode is not DesiredMode.LIVE:
            reasons.append("VENUE_NOT_IN_LIVE_MODE")
        if request.venue.value not in mandate.allowed_venues:
            reasons.append("VENUE_NOT_IN_MANDATE")
        elif mandate.account_hashes.get(request.venue.value) != request.account_hash:
            reasons.append("ACCOUNT_HASH_MISMATCH")
        requested_instrument_types = {
            instrument.split(":", 1)[0] for instrument in request.authorized_instruments
        }
        for instrument_type in sorted(
            requested_instrument_types - set(mandate.allowed_instruments)
        ):
            reasons.append(f"INSTRUMENT_NOT_IN_MANDATE:{instrument_type}")
        if request.venue.value not in snapshot.reconciled_venues:
            reasons.append("VENUE_NOT_RECONCILED")
        if observed_at.astimezone(timezone.utc) - snapshot.observed_at.astimezone(timezone.utc) > timedelta(seconds=60):
            reasons.append("PORTFOLIO_SNAPSHOT_STALE")
        if snapshot.observed_at.astimezone(timezone.utc) > observed_at.astimezone(timezone.utc) + MAX_FUTURE_SKEW:
            reasons.append("PORTFOLIO_SNAPSHOT_FROM_FUTURE")
        if snapshot.combined_daily_loss_cents >= daily_limit:
            reasons.append("DAILY_LOSS_STOP")
        if snapshot.venue_daily_loss_cents[request.venue.value] >= venue_daily_limit:
            reasons.append("VENUE_DAILY_LOSS_STOP")
        if snapshot.high_water_drawdown_cents >= drawdown_limit:
            reasons.append("DRAWDOWN_STOP")
        if snapshot.combined_open_risk_cents > combined_limit:
            reasons.append("CURRENT_COMBINED_RISK_EXCEEDS_LIMIT")
        if request.max_open_risk_cents > venue_limit:
            reasons.append("REQUESTED_VENUE_RISK_EXCEEDS_LIMIT")
        projected_combined = (
            snapshot.combined_open_risk_cents
            - snapshot.venue_open_risk_cents[request.venue.value]
            + request.max_open_risk_cents
        )
        if projected_combined > combined_limit:
            reasons.append("PROJECTED_COMBINED_RISK_EXCEEDS_LIMIT")
        if request.max_order_risk_cents > idea_limit:
            reasons.append("ORDER_RISK_EXCEEDS_IDEA_LIMIT")
        if request.max_order_risk_cents > request.max_open_risk_cents:
            reasons.append("ORDER_RISK_EXCEEDS_VENUE_RISK")
        if request.max_order_risk_cents > request.max_correlated_risk_cents:
            reasons.append("ORDER_RISK_EXCEEDS_CORRELATED_RISK")
        if request.max_correlated_risk_cents > correlated_limit:
            reasons.append("CORRELATED_RISK_EXCEEDS_LIMIT")
        if request.max_correlated_risk_cents > request.max_open_risk_cents:
            reasons.append("CORRELATED_RISK_EXCEEDS_VENUE_RISK")
        current_cluster = snapshot.correlated_open_risk_cents.get(request.correlation_cluster, 0)
        if current_cluster > request.max_correlated_risk_cents:
            reasons.append("CURRENT_CLUSTER_RISK_EXCEEDS_REQUESTED_LIMIT")
        if request.max_daily_loss_cents > venue_daily_limit:
            reasons.append("REQUESTED_VENUE_DAILY_LOSS_EXCEEDS_LIMIT")
        if request.max_daily_loss_cents > daily_limit:
            reasons.append("REQUESTED_DAILY_LOSS_EXCEEDS_LIMIT")
        if request.max_open_orders > self.policy.max_open_orders_per_venue:
            reasons.append("OPEN_ORDER_LIMIT_EXCEEDED")
        if snapshot.venue_open_orders[request.venue.value] > request.max_open_orders:
            reasons.append("CURRENT_OPEN_ORDERS_EXCEED_REQUEST")
        if snapshot.venue_open_positions[request.venue.value] > self.policy.max_positions_per_venue:
            reasons.append("CURRENT_OPEN_POSITIONS_EXCEED_POLICY")
        return CapitalDecision(
            allowed=not reasons,
            reason_codes=tuple(sorted(reasons)) if reasons else ("CAPITAL_REQUEST_ALLOWED",),
            effective_nav_cents=effective_nav,
            combined_limit_cents=combined_limit,
            venue_limit_cents=venue_limit,
            idea_limit_cents=idea_limit,
            correlated_limit_cents=correlated_limit,
            daily_loss_limit_cents=daily_limit,
            venue_daily_loss_limit_cents=venue_daily_limit,
            drawdown_limit_cents=drawdown_limit,
        )
