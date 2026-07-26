from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from blunder.chronos import ChronosContext, build_chronos_stamp
from blunder.resource_accounting import (
    RESOURCE_METRIC_NAMES,
    build_resource_charge,
    measured,
    unmeasured,
)
from blunder.physiology import (
    HealthThresholds,
    Opportunity,
    build_routing_tombstone,
    cognitive_physics,
    derive_health_vector,
    homeostatic_plan,
    select_opportunities,
    tombstone_status,
)
from blunder.validation_broker import BrokerTimeoutExpired, brokered_run
from blunder.missions.worker import execute


class BlunderPhysiologyTests(unittest.TestCase):
    def resource_charge(
        self,
        cpu_time: int,
        peak_memory: int | None,
        queue_depth: int | None,
    ) -> dict[str, object]:
        now = datetime.now(timezone.utc)
        context = ChronosContext(time.monotonic_ns(), "ccos-epoch-1", 0)
        memory = (
            measured(peak_memory, "bytes", "fixture")
            if peak_memory is not None
            else unmeasured("bytes", "fixture", "FIXTURE_UNMEASURED")
        )
        local_metrics = {
            "processDurationNanoseconds": measured(100, "ns", "fixture"),
            "cpuTimeNanoseconds": measured(cpu_time, "ns", "fixture"),
            "peakMemoryBytes": memory,
            "diskReadBytes": measured(20, "bytes", "fixture"),
            "diskWriteBytes": measured(10, "bytes", "fixture"),
        }
        return build_resource_charge(
            "fixture:health",
            "native-mission",
            context,
            local_metrics,
            {"providerCalls": 0, "totalTokens": 0, "costUsd": 0.0},
            "fixture-provider-proof",
            queue_depth,
            10 if queue_depth is not None else None,
            now,
        )

    def test_chronos_preserves_separate_clock_policy_and_generation_domains(self) -> None:
        event_time = datetime(2026, 7, 14, 20, 0, tzinfo=timezone.utc)
        context = ChronosContext(1_000, "ccos-epoch-1", 0)
        stamp = build_chronos_stamp(
            context,
            4_000,
            event_time,
            event_time - timedelta(seconds=2),
            5.0,
        )
        self.assertEqual(stamp["monotonicMissionNanoseconds"], 3_000)
        self.assertEqual(stamp["wallClockEventTime"], "2026-07-14T20:00:00Z")
        self.assertEqual(stamp["evidenceFreshness"]["state"], "FRESH")
        self.assertEqual(stamp["evidenceFreshness"]["ageNanoseconds"], 2_000_000_000)
        self.assertEqual(stamp["policyEpoch"], "ccos-epoch-1")
        self.assertEqual(stamp["evolutionGeneration"], 0)

    def test_resource_charge_keeps_missing_queue_telemetry_explicit(self) -> None:
        now = datetime.now(timezone.utc)
        context = ChronosContext(1_000, "ccos-epoch-1", 0)
        local_metrics = {
            "processDurationNanoseconds": measured(50, "ns", "fixture"),
            "cpuTimeNanoseconds": measured(40, "ns", "fixture"),
            "peakMemoryBytes": measured(30, "bytes", "fixture"),
            "diskReadBytes": measured(20, "bytes", "fixture"),
            "diskWriteBytes": measured(10, "bytes", "fixture"),
        }
        with self.subTest("explicit partial vector"):
            charge = build_resource_charge(
                "fixture:charge",
                "validation-shard",
                context,
                local_metrics,
                {"providerCalls": 0, "totalTokens": 0, "costUsd": 0.0},
                "fixture-provider-proof",
                None,
                None,
                now,
            )
        self.assertEqual(charge["completeness"], "PARTIAL")
        self.assertEqual(charge["unmeasured"], ["queueDepth", "queueCapacity"])
        self.assertEqual(tuple(charge["metrics"]), RESOURCE_METRIC_NAMES)
        self.assertEqual(charge["metrics"]["queueDepth"]["state"], "UNMEASURED")
        self.assertFalse(charge["authority"]["optimization"])
        self.assertFalse(charge["authority"]["homeostaticAction"])

    def test_validation_broker_returns_a_complete_or_explicit_partial_vector(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = brokered_run(
                (sys.executable, "-c", "print('physiology-proof')"),
                Path(directory),
                dict(os.environ),
                30,
                128 * 1024 * 1024,
            )
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["telemetry"]["processDurationNanoseconds"]["state"], "MEASURED")
        self.assertEqual(
            set(result["telemetry"]),
            {
                "processDurationNanoseconds",
                "cpuTimeNanoseconds",
                "peakMemoryBytes",
                "diskReadBytes",
                "diskWriteBytes",
            },
        )
        self.assertTrue(
            all(metric["state"] in {"MEASURED", "UNMEASURED"} for metric in result["telemetry"].values())
        )

    def test_failed_mission_worker_still_emits_an_explicit_resource_charge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result_path = root / "worker-result.json"
            exit_code = execute(root, "bounded worker failure proof", False, result_path)
            result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(exit_code, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["resourceCharge"]["completeness"], "PARTIAL")
        self.assertEqual(result["resourceCharge"]["metrics"]["providerCalls"]["state"], "UNMEASURED")
        self.assertEqual(result["resourceCharge"]["metrics"]["queueDepth"]["state"], "UNMEASURED")

    def test_validation_timeout_preserves_resource_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(BrokerTimeoutExpired) as caught:
            brokered_run(
                (sys.executable, "-c", "import time; time.sleep(2)"),
                Path(directory),
                dict(os.environ),
                1,
                128 * 1024 * 1024,
            )
        self.assertEqual(caught.exception.telemetry["processDurationNanoseconds"]["state"], "MEASURED")
        self.assertTrue(
            all(
                metric["state"] in {"MEASURED", "UNMEASURED"}
                for metric in caught.exception.telemetry.values()
            )
        )

    def test_health_is_deterministic_hysteretic_and_never_infers_unknown_as_healthy(self) -> None:
        thresholds = HealthThresholds(50.0, 1_000, 0.8, 1_000, 0.8, 0.6)
        healthy = derive_health_vector(self.resource_charge(10, 100, 1), thresholds, None)
        overloaded = derive_health_vector(self.resource_charge(90, 100, 1), thresholds, "HEALTHY")
        recovery_bound = derive_health_vector(self.resource_charge(35, 100, 1), thresholds, "OVERLOADED")
        unknown = derive_health_vector(self.resource_charge(10, None, 1), thresholds, None)
        self.assertEqual(healthy["status"], "HEALTHY")
        self.assertEqual(overloaded["status"], "OVERLOADED")
        self.assertEqual(recovery_bound["status"], "RECOVERY_BOUND")
        self.assertEqual(unknown["status"], "UNKNOWN")
        self.assertIn("peakMemoryBytes", unknown["unknownMetrics"])

    def test_homeostasis_only_reduces_authority_neutral_limits(self) -> None:
        overloaded = homeostatic_plan("OVERLOADED", 4, 8)
        unknown = homeostatic_plan("UNKNOWN", 4, 8)
        self.assertEqual(overloaded["limits"], {"parallelism": 3, "candidateBudget": 4})
        self.assertIn("QUARANTINE_MUTATION", overloaded["actions"])
        self.assertIn("REQUEST_OPERATOR_REVIEW", unknown["actions"])
        self.assertFalse(overloaded["authority"]["authorityExpansion"])
        self.assertFalse(overloaded["authority"]["sourceMutation"])

    def test_typed_opportunities_respect_budgets_and_reversible_tombstones(self) -> None:
        opportunities = (
            Opportunity("alpha", "proof", 100, 20, "A" * 64, "First proof"),
            Opportunity("beta", "analysis", 90, 20, "B" * 64, "Second proof"),
        )
        selected = select_opportunities(opportunities, 2, 40, "HEALTHY", ("alpha",))
        paused = select_opportunities(opportunities, 2, 40, "UNKNOWN", ())
        now = datetime.now(timezone.utc)
        tombstone = build_routing_tombstone(
            "alpha",
            now,
            now + timedelta(hours=1),
            ("A" * 64,),
            "REPEATED_VALIDATION_FAILURE",
            False,
        )
        active = tombstone_status(tombstone, now + timedelta(minutes=1))
        expired = tombstone_status(tombstone, now + timedelta(hours=2))
        self.assertEqual([item["id"] for item in selected["selected"]], ["beta"])
        self.assertEqual(paused["selected"], [])
        self.assertTrue(active["routingBlocked"])
        self.assertFalse(expired["routingBlocked"])
        self.assertTrue(expired["replayable"])
        self.assertFalse(expired["sourceDeleted"])

    def test_cognitive_physics_uses_declared_measured_definitions(self) -> None:
        physics = cognitive_physics(
            (0.5, 0.5),
            0.2,
            0.6,
            2.0,
            ({"impact": 4.0, "uncertainty": 0.5, "estimatedCost": 2.0},),
        )
        self.assertEqual(physics["entropyBits"], 1.0)
        self.assertEqual(physics["normalizedEntropy"], 1.0)
        self.assertEqual(physics["momentumUtilityPerSecond"], 0.2)
        self.assertEqual(physics["potential"], 1.0)
        self.assertFalse(physics["authority"]["selectionAuthority"])


if __name__ == "__main__":
    unittest.main()
