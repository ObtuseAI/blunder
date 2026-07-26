"""Typed clock-domain evidence for Blunder missions and validation work."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone


CHRONOS_SCHEMA = "obtuse.blunder.chronos.v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


@dataclass(frozen=True)
class ChronosContext:
    mission_started_monotonic_ns: int
    policy_epoch: str
    evolution_generation: int


def start_chronos_context(policy_epoch: str, evolution_generation: int) -> ChronosContext:
    """Create an explicit mission clock context without conflating clock domains."""

    if _IDENTIFIER_PATTERN.fullmatch(policy_epoch) is None:
        raise ValueError(f"Chronos policy epoch is invalid: {policy_epoch}")
    if not isinstance(evolution_generation, int) or isinstance(evolution_generation, bool) or evolution_generation < 0:
        raise ValueError("Chronos evolution generation must be a non-negative integer.")
    return ChronosContext(time.monotonic_ns(), policy_epoch, evolution_generation)


def _utc(value: datetime, context: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{context} must include an explicit timezone.")
    return value.astimezone(timezone.utc)


def build_chronos_stamp(
    context: ChronosContext,
    event_monotonic_ns: int,
    event_wall_time: datetime,
    evidence_observed_at: datetime | None,
    evidence_max_age_seconds: float | None,
) -> dict[str, object]:
    """Build a deterministic stamp from explicitly supplied clock readings."""

    if event_monotonic_ns < context.mission_started_monotonic_ns:
        raise ValueError("Chronos event monotonic time precedes the mission start.")
    event_utc = _utc(event_wall_time, "Chronos event wall time")
    if evidence_observed_at is None or evidence_max_age_seconds is None:
        freshness: dict[str, object] = {
            "state": "UNMEASURED",
            "observedAt": None,
            "ageNanoseconds": None,
            "maximumAgeNanoseconds": None,
            "reason": "EVIDENCE_FRESHNESS_INPUT_MISSING",
        }
    else:
        if evidence_max_age_seconds < 0:
            raise ValueError("Chronos evidence maximum age must be non-negative.")
        evidence_utc = _utc(evidence_observed_at, "Chronos evidence observed time")
        age_nanoseconds = int((event_utc - evidence_utc).total_seconds() * 1_000_000_000)
        maximum_age_nanoseconds = int(evidence_max_age_seconds * 1_000_000_000)
        freshness = {
            "state": (
                "UNKNOWN_CLOCK_SKEW"
                if age_nanoseconds < 0
                else "FRESH"
                if age_nanoseconds <= maximum_age_nanoseconds
                else "STALE"
            ),
            "observedAt": evidence_utc.isoformat().replace("+00:00", "Z"),
            "ageNanoseconds": age_nanoseconds,
            "maximumAgeNanoseconds": maximum_age_nanoseconds,
            "reason": "WALL_CLOCK_SKEW" if age_nanoseconds < 0 else None,
        }
    return {
        "schema": CHRONOS_SCHEMA,
        "monotonicMissionNanoseconds": event_monotonic_ns - context.mission_started_monotonic_ns,
        "wallClockEventTime": event_utc.isoformat().replace("+00:00", "Z"),
        "evidenceFreshness": freshness,
        "policyEpoch": context.policy_epoch,
        "evolutionGeneration": context.evolution_generation,
    }


def chronos_now(
    context: ChronosContext,
    evidence_observed_at: datetime | None,
    evidence_max_age_seconds: float | None,
) -> dict[str, object]:
    """Sample current monotonic and UTC clocks into the separated Chronos contract."""

    return build_chronos_stamp(
        context,
        time.monotonic_ns(),
        datetime.now(timezone.utc),
        evidence_observed_at,
        evidence_max_age_seconds,
    )
