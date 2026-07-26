from __future__ import annotations

import json
import os
import tempfile
import unittest
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from blunder.ccos_runtime import (
    CONFIG_SCHEMA,
    RECOVERY_CONFIRMATION,
    CCOSRuntimeConfig,
    append_runtime_record,
    inspect_runtime_recovery,
    load_runtime_config,
    provider_free_canary,
    recover_last_known_good,
    retention_report,
    runtime_records,
    runtime_state,
)


def runtime_config(root: Path) -> CCOSRuntimeConfig:
    return CCOSRuntimeConfig(
        schema=CONFIG_SCHEMA,
        artifact_root=root / "runtime",
        maximum_record_bytes=32_768,
        maximum_records=10_000,
        lock_timeout_seconds=3.0,
        stale_lock_seconds=30.0,
        retention_days={
            "genome": 3650,
            "evaluation": 3650,
            "memory": 3650,
            "lineage": 3650,
            "observatory": 3650,
            "quarantine": 3650,
            "recovery": 3650,
            "canary": 90,
        },
        provider_calls_allowed=False,
        automatic_activation_allowed=False,
        federation_allowed=False,
        meta_evolution_allowed=False,
    )


def write_process_record(arguments: tuple[str, int]) -> str:
    root, index = arguments
    record = append_runtime_record(
        runtime_config(Path(root)),
        "evaluation",
        {"processCase": index, "status": "HELD_OUT_NO_CHAMPION"},
        datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc) + timedelta(microseconds=index),
    )
    return str(record["recordDigest"])


def write_config(root: Path, artifact_root: str, provider_calls_allowed: bool) -> Path:
    path = root / "ccos.json"
    path.write_text(
        json.dumps(
            {
                "schema": CONFIG_SCHEMA,
                "artifactRoot": artifact_root,
                "maximumRecordBytes": 32768,
                "maximumRecords": 10000,
                "lockTimeoutSeconds": 3,
                "staleLockSeconds": 30,
                "retentionDays": {
                    "genome": 3650,
                    "evaluation": 3650,
                    "memory": 3650,
                    "lineage": 3650,
                    "observatory": 3650,
                    "quarantine": 3650,
                    "recovery": 3650,
                    "canary": 90,
                },
                "authority": {
                    "providerCallsAllowed": provider_calls_allowed,
                    "automaticActivationAllowed": False,
                    "federationAllowed": False,
                    "metaEvolutionAllowed": False,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


class CCOSRuntimeTests(unittest.TestCase):
    def test_configuration_is_explicit_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_runtime_config(root, write_config(root, "artifacts/ccos", False))
            self.assertEqual(config.artifact_root, (root / "artifacts" / "ccos").resolve())
            self.assertFalse(config.provider_calls_allowed)
            with self.assertRaises(PermissionError):
                load_runtime_config(root, write_config(root, "artifacts/ccos", True))
            with self.assertRaises(ValueError):
                load_runtime_config(root, write_config(root, "../escape", False))
            with self.assertRaises(ValueError):
                load_runtime_config(root, write_config(root, "blunder/runtime", False))
            duplicate_path = write_config(root, "artifacts/ccos", False)
            original = duplicate_path.read_text(encoding="utf-8")
            duplicate_path.write_text(original.replace('{"schema":', '{"schema":"duplicate","schema":', 1), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_runtime_config(root, duplicate_path)

    def test_records_round_trip_with_content_and_chain_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = runtime_config(Path(temporary))
            observed_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
            first = append_runtime_record(
                config,
                "genome",
                {"genomeDigest": "A" * 64, "fitnessStatus": "UNMEASURED"},
                observed_at,
            )
            second = append_runtime_record(
                config,
                "memory",
                {"memoryId": "B" * 64, "transition": "PROMOTION_BLOCKED"},
                observed_at + timedelta(seconds=1),
            )
            records = runtime_records(config, ())
            state = runtime_state(config)
            self.assertEqual([record["recordDigest"] for record in records], [first["recordDigest"], second["recordDigest"]])
            self.assertEqual(second["previousDigest"], first["recordDigest"])
            self.assertEqual(state["generation"], 2)
            self.assertEqual(state["counts"]["genome"], 1)
            self.assertFalse(state["authority"]["automaticActivationAllowed"])
            self.assertFalse(second["authorityGranted"])

    def test_payload_rejects_authority_and_private_reasoning_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = runtime_config(Path(temporary))
            now = datetime.now(timezone.utc)
            for forbidden in ("activation", "credentials", "chain_of_thought", "private-memory", "Token"):
                with self.subTest(forbidden=forbidden), self.assertRaises(PermissionError):
                    append_runtime_record(config, "observatory", {"nested": {forbidden: "value"}}, now)
            for non_finite in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(non_finite=non_finite), self.assertRaises(ValueError):
                    append_runtime_record(config, "observatory", {"measurement": non_finite}, now)
            nested: dict[str, object] = {"value": "bounded"}
            for index in range(34):
                nested = {f"level{index}": nested}
            with self.assertRaises(ValueError):
                append_runtime_record(config, "observatory", nested, now)
            with self.assertRaises(PermissionError):
                append_runtime_record(
                    replace(config, automatic_activation_allowed=True),
                    "observatory",
                    {"status": "OBSERVED"},
                    now,
                )
            self.assertEqual(runtime_state(config)["generation"], 0)

    def test_content_identity_is_stable_across_mapping_order(self) -> None:
        with tempfile.TemporaryDirectory() as first_temporary, tempfile.TemporaryDirectory() as second_temporary:
            first_config = runtime_config(Path(first_temporary))
            second_config = runtime_config(Path(second_temporary))
            observed_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
            first = append_runtime_record(
                first_config,
                "evaluation",
                {"alpha": 1, "nested": {"beta": 2, "gamma": [3, 4]}},
                observed_at,
            )
            second = append_runtime_record(
                second_config,
                "evaluation",
                {"nested": {"gamma": [3, 4], "beta": 2}, "alpha": 1},
                observed_at,
            )
            self.assertEqual(first["payloadDigest"], second["payloadDigest"])
            self.assertEqual(first["recordDigest"], second["recordDigest"])

    def test_concurrent_writers_preserve_every_record_and_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = runtime_config(Path(temporary))
            observed_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

            def write_record(index: int) -> str:
                record = append_runtime_record(
                    config,
                    "evaluation",
                    {"case": index, "status": "HELD_OUT_NO_CHAMPION"},
                    observed_at + timedelta(microseconds=index),
                )
                return str(record["recordDigest"])

            with ThreadPoolExecutor(max_workers=8) as executor:
                digests = tuple(executor.map(write_record, range(24)))
            records = runtime_records(config, ("evaluation",))
            self.assertEqual(len(records), 24)
            self.assertEqual(len(set(digests)), 24)
            self.assertEqual([record["sequence"] for record in records], list(range(24)))

    def test_cross_process_writers_preserve_one_verified_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = runtime_config(Path(temporary))
            arguments = tuple((temporary, index) for index in range(8))
            with ProcessPoolExecutor(max_workers=4) as executor:
                digests = tuple(executor.map(write_process_record, arguments))
            records = runtime_records(config, ("evaluation",))
            self.assertEqual(len(records), 8)
            self.assertEqual(len(set(digests)), 8)
            self.assertEqual([record["sequence"] for record in records], list(range(8)))

    def test_tampering_fails_closed_and_confirmed_recovery_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = runtime_config(Path(temporary))
            now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
            first = append_runtime_record(config, "recovery", {"checkpoint": "one"}, now)
            append_runtime_record(config, "recovery", {"checkpoint": "two"}, now + timedelta(seconds=1))
            index_path = config.artifact_root / "index.json"
            index_path.write_text('{"corrupt":true}\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                runtime_state(config)
            with self.assertRaises(PermissionError):
                recover_last_known_good(config, "recover")
            result = recover_last_known_good(config, RECOVERY_CONFIRMATION)
            self.assertEqual(result["status"], "RECOVERED_LAST_KNOWN_GOOD")
            self.assertEqual(result["generation"], 1)
            self.assertEqual(result["headDigest"], first["recordDigest"])
            self.assertEqual(runtime_state(config)["generation"], 1)
            inspection = inspect_runtime_recovery(config, now + timedelta(seconds=2))
            self.assertEqual(inspection["status"], "RECOVERY_REVIEW_REQUIRED")
            self.assertEqual(inspection["indexStatus"], "VERIFIED")
            self.assertEqual(inspection["validOrphanRecordDigests"], [
                path.stem
                for path in sorted((config.artifact_root / "records").glob("*.json"))
                if path.stem != first["recordDigest"]
            ])
            self.assertFalse(inspection["automaticRepair"])

    def test_recovery_never_steals_a_live_writer_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = runtime_config(Path(temporary))
            now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
            append_runtime_record(config, "recovery", {"checkpoint": "one"}, now)
            append_runtime_record(config, "recovery", {"checkpoint": "two"}, now + timedelta(seconds=1))
            lock_path = config.artifact_root / ".writer-lock"
            lock_path.write_text(f"pid={os.getpid()}\n", encoding="ascii")
            stale_time = (now - timedelta(days=1)).timestamp()
            os.utime(lock_path, (stale_time, stale_time))
            with self.assertRaises(TimeoutError):
                recover_last_known_good(config, RECOVERY_CONFIRMATION)
            self.assertTrue(lock_path.is_file())
            self.assertFalse((config.artifact_root / ".recovery-lock").exists())
            lock_path.unlink()

    def test_recovery_quarantines_only_a_stale_dead_writer_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = runtime_config(Path(temporary))
            now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
            first = append_runtime_record(config, "recovery", {"checkpoint": "one"}, now)
            append_runtime_record(config, "recovery", {"checkpoint": "two"}, now + timedelta(seconds=1))
            lock_path = config.artifact_root / ".writer-lock"
            lock_path.write_text("pid=2147483647\n", encoding="ascii")
            stale_time = (now - timedelta(days=1)).timestamp()
            os.utime(lock_path, (stale_time, stale_time))
            result = recover_last_known_good(config, RECOVERY_CONFIRMATION)
            self.assertEqual(result["headDigest"], first["recordDigest"])
            self.assertFalse(lock_path.exists())
            self.assertEqual(len(tuple((config.artifact_root / "recovery").glob("stale-lock-*"))), 1)

    def test_provider_free_canary_persists_honest_no_champion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = runtime_config(Path(temporary))
            result = provider_free_canary(config, datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc))
            self.assertEqual(result["status"], "PASS")
            self.assertTrue(result["exactReload"])
            self.assertEqual(result["heldOutEvaluation"], "NO_CHAMPION_INSUFFICIENT_REALITY_EVIDENCE")
            self.assertEqual(result["providerCalls"], 0)
            self.assertFalse(result["authorityGranted"])

    def test_retention_never_deletes_canonical_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = runtime_config(Path(temporary))
            observed_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
            record = append_runtime_record(
                config,
                "canary",
                {"status": "HISTORICAL_EVIDENCE"},
                observed_at - timedelta(days=91),
            )
            report = retention_report(config, observed_at)
            self.assertEqual(report["status"], "REVIEW_REQUIRED")
            self.assertEqual(report["candidateCount"], 1)
            self.assertEqual(report["candidates"][0]["recommendedAction"], "TOMBSTONE_PROJECTION_ONLY")
            self.assertTrue(report["candidates"][0]["canonicalEvidencePreserved"])
            self.assertEqual(report["canonicalRecordsDeleted"], 0)
            self.assertFalse(report["automaticDeletion"])
            self.assertEqual(runtime_records(config, ("canary",))[0]["recordDigest"], record["recordDigest"])


if __name__ == "__main__":
    unittest.main()
