from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from blunder.ccos_canary import (
    ACTIVATION_CANARY_CONFIRMATION,
    ROLLBACK_CANARY_CONFIRMATION,
    run_operational_canary,
)
from blunder.ccos_runtime import append_runtime_record, runtime_records, runtime_state
from tests.test_blunder_ccos_runtime import runtime_config


class CCOSOperationalCanaryTests(unittest.TestCase):
    def test_confirmations_are_exact_and_no_partial_evidence_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = runtime_config(Path(temporary))
            observed_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
            with self.assertRaises(PermissionError):
                run_operational_canary(config, observed_at, "simulate", ROLLBACK_CANARY_CONFIRMATION)
            with self.assertRaises(PermissionError):
                run_operational_canary(config, observed_at, ACTIVATION_CANARY_CONFIRMATION, "rollback")
            self.assertEqual(runtime_state(config)["generation"], 0)

    def test_full_canary_persists_each_plane_and_preserves_no_champion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = runtime_config(Path(temporary))
            report = run_operational_canary(
                config,
                datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
                ACTIVATION_CANARY_CONFIRMATION,
                ROLLBACK_CANARY_CONFIRMATION,
            )
            state = runtime_state(config)
            self.assertEqual(report["status"], "PASS_AUTHORITY_CLOSED_NO_CHAMPION")
            self.assertEqual(report["heldOutEvaluation"], "NO_CHAMPION")
            self.assertEqual(report["activationCanary"], "BLOCKED_BY_CONFIGURATION")
            self.assertEqual(report["rollbackCanary"], "VERIFIED_NO_EFFECT_TO_ROLL_BACK")
            self.assertFalse(report["authorityGranted"])
            self.assertFalse(report["liveTreeChanged"])
            self.assertEqual(report["providerCalls"], 0)
            self.assertEqual(report["runtimeGeneration"], 7)
            self.assertEqual(report["canaryRecordDigest"], state["headDigest"])
            self.assertEqual(report["runtimeIndexDigest"], state["indexDigest"])
            self.assertEqual(state["generation"], 7)
            for kind in ("genome", "lineage", "evaluation", "memory", "observatory", "quarantine", "canary"):
                with self.subTest(kind=kind):
                    self.assertEqual(len(runtime_records(config, (kind,))), 1)

    def test_canary_refuses_insufficient_capacity_before_partial_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = replace(runtime_config(Path(temporary)), maximum_records=8)
            observed_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
            append_runtime_record(config, "recovery", {"checkpoint": "one"}, observed_at)
            append_runtime_record(config, "recovery", {"checkpoint": "two"}, observed_at)
            before = runtime_state(config)
            with self.assertRaises(RuntimeError):
                run_operational_canary(
                    config,
                    observed_at,
                    ACTIVATION_CANARY_CONFIRMATION,
                    ROLLBACK_CANARY_CONFIRMATION,
                )
            after = runtime_state(config)
            self.assertEqual(after["generation"], before["generation"])
            self.assertEqual(after["indexDigest"], before["indexDigest"])


if __name__ == "__main__":
    unittest.main()
