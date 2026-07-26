from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from blunder.chronos import ChronosContext
from blunder.constitutional_kernel import load_runtime_constitution
from blunder.resource_accounting import build_resource_charge, sample_local_process


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def environment_int(name: str, fallback: int) -> int:
    value = os.environ.get(name, "").strip()
    return int(value) if value else fallback


def environment_float(name: str, fallback: float) -> float:
    value = os.environ.get(name, "").strip()
    return float(value) if value else fallback


def optional_environment_int(name: str) -> int | None:
    value = os.environ.get(name, "").strip()
    return int(value) if value else None


@dataclass
class ExecutionBudget:
    maximum_seconds: int
    maximum_provider_calls: int
    maximum_tokens: int
    maximum_cost_usd: float
    event_log: Path | None
    cache_root: Path | None = None
    started_monotonic: float = field(default_factory=time.monotonic)
    provider_calls: int = 0
    provider_attempts: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    @classmethod
    def from_environment(cls) -> "ExecutionBudget":
        event_value = os.environ.get("BLUNDER_MISSION_EVENT_LOG", "").strip()
        return cls(
            maximum_seconds=environment_int("BLUNDER_MISSION_MAX_SECONDS", 1_800),
            maximum_provider_calls=environment_int("BLUNDER_MISSION_MAX_PROVIDER_CALLS", 3),
            maximum_tokens=environment_int("BLUNDER_MISSION_MAX_TOKENS", 200_000),
            maximum_cost_usd=environment_float("BLUNDER_MISSION_MAX_COST_USD", 5.0),
            event_log=Path(event_value).resolve() if event_value else None,
            cache_root=None,
        )

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_monotonic

    def enforce(self, stage: str) -> None:
        if self.elapsed_seconds() > self.maximum_seconds:
            raise TimeoutError(f"Mission budget exceeded {self.maximum_seconds} seconds during {stage}.")
        if self.provider_calls > self.maximum_provider_calls:
            raise RuntimeError(f"Mission provider-call budget exceeded during {stage}.")
        if self.prompt_tokens + self.completion_tokens > self.maximum_tokens:
            raise RuntimeError(f"Mission token budget exceeded during {stage}.")
        if self.cost_usd > self.maximum_cost_usd:
            raise RuntimeError(f"Mission cost budget exceeded ${self.maximum_cost_usd:.4f} during {stage}.")

    def record(self, event: str, fields: Mapping[str, object]) -> None:
        if self.event_log is None:
            return
        self.event_log.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": utc_now(), "event": event, **dict(fields)}
        with self.event_log.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")

    def begin_provider(self, stage: str) -> None:
        self.enforce(stage)
        self.provider_calls += 1
        self.enforce(stage)
        self.record("provider.started", {"stage": stage, "providerCalls": self.provider_calls})

    def provider_attempt(self, stage: str, attempt: int) -> None:
        self.provider_attempts += 1
        self.enforce(stage)
        self.record("provider.attempted", {"stage": stage, "attempt": attempt})

    def complete_provider(self, stage: str, usage: Mapping[str, object]) -> None:
        prompt_tokens = int(usage.get("prompt_tokens") or usage.get("promptTokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or usage.get("completionTokens") or 0)
        if prompt_tokens + completion_tokens < 1 and not isinstance(usage.get("cost"), (int, float)):
            raise RuntimeError(f"Provider omitted usage evidence required by the mission budget during {stage}.")
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        cost = usage.get("cost")
        if isinstance(cost, (int, float)):
            self.cost_usd += float(cost)
        else:
            input_rate = environment_float("BLUNDER_INPUT_COST_PER_MILLION", 5.0)
            output_rate = environment_float("BLUNDER_OUTPUT_COST_PER_MILLION", 15.0)
            self.cost_usd += (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000
        self.record("provider.completed", {"stage": stage, "usage": dict(usage), **self.public()})
        self.enforce(stage)

    def public(self) -> dict[str, object]:
        return {
            "elapsedSeconds": round(self.elapsed_seconds(), 3),
            "providerCalls": self.provider_calls,
            "providerAttempts": self.provider_attempts,
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "totalTokens": self.prompt_tokens + self.completion_tokens,
            "costUsd": round(self.cost_usd, 6),
            "limits": {
                "seconds": self.maximum_seconds,
                "providerCalls": self.maximum_provider_calls,
                "tokens": self.maximum_tokens,
                "costUsd": self.maximum_cost_usd,
            },
        }

    def resource_charge(
        self,
        charge_id: str,
        scope: str,
        queue_depth: int | None,
        queue_capacity: int | None,
    ) -> dict[str, object]:
        constitution = load_runtime_constitution()
        context = ChronosContext(
            int(self.started_monotonic * 1_000_000_000),
            constitution.policy_epoch,
            0,
        )
        observed_at = datetime.now(timezone.utc)
        charge = build_resource_charge(
            charge_id,
            scope,
            context,
            sample_local_process(context.mission_started_monotonic_ns),
            self.public(),
            "execution-budget-validated-usage",
            queue_depth,
            queue_capacity,
            observed_at,
        )
        self.record("resource.charged", charge)
        return charge
