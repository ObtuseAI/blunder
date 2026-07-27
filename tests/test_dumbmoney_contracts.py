from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from blunder.fund.canonical import (
    CanonicalizationError,
    bytes_sha256,
    canonical_json_bytes,
    canonical_sha256,
    load_strict_json,
    loads_strict_json,
)
from blunder.fund.contracts import (
    CapitalEnvelopeV1,
    CellJournalHeadAnchorV1,
    DesiredMode,
    EvidenceClass,
    MigrationManifestV1,
    OutcomeSettlementV1,
    ReconciliationStatus,
    SignedEnvelopeV1,
    Venue,
    parse_contract,
)
from blunder.fund.crypto import (
    Ed25519Keyring,
    Ed25519Signer,
    SignatureVerificationError,
    decode_base64url,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "configs" / "dumbmoney" / "fixtures" / "signed-capital-envelope.v1.json"
DOOFUS_FIXTURE_ROOT = (
    Path(r"C:\src\engine\doofus-dumbmoney")
    / "doofus"
    / "dumbmoney"
    / "interop_fixtures"
)


class CanonicalContractTests(unittest.TestCase):
    def test_canonical_json_is_stable_unicode_compact_and_float_free(self) -> None:
        first = {"z": [3, 2], "a": "café", "nested": {"b": True, "a": None}}
        second = {"nested": {"a": None, "b": True}, "a": "café", "z": [3, 2]}
        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
        self.assertEqual(
            canonical_json_bytes(first),
            '{"a":"café","nested":{"a":null,"b":true},"z":[3,2]}'.encode(),
        )
        with self.assertRaisesRegex(CanonicalizationError, "float"):
            canonical_json_bytes({"money": 1.25})
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            loads_strict_json('{"same":1,"same":2}')

    def test_deterministic_cross_repo_fixture_verifies(self) -> None:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        envelope = SignedEnvelopeV1.from_dict(fixture["envelope"])
        keyring = Ed25519Keyring()
        key_id = keyring.register(decode_base64url(fixture["public_key_base64url"], "fixture public key"))
        self.assertEqual(key_id, envelope.signer_key_id)
        self.assertEqual(bytes_sha256(envelope.signing_bytes()), fixture["signing_payload_sha256"])
        parsed = envelope.verify(keyring, datetime(2099, 1, 1, 0, 0, 30, tzinfo=timezone.utc))
        self.assertIsInstance(parsed, CapitalEnvelopeV1)
        self.assertEqual(parsed.max_order_risk_cents, 100)
        self.assertEqual(parsed.policy_epoch, 1)

    def test_envelope_detects_body_event_signature_and_time_tampering(self) -> None:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        keyring = Ed25519Keyring()
        keyring.register(decode_base64url(fixture["public_key_base64url"], "fixture public key"))
        base = fixture["envelope"]
        tampered_body = json.loads(json.dumps(base))
        tampered_body["body"]["max_order_risk_cents"] = 101
        with self.assertRaisesRegex(ValueError, "body digest"):
            SignedEnvelopeV1.from_dict(tampered_body).verify(
                keyring, datetime(2099, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
            )
        tampered_event = json.loads(json.dumps(base))
        tampered_event["event_id"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "event ID"):
            SignedEnvelopeV1.from_dict(tampered_event).verify(
                keyring, datetime(2099, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
            )
        tampered_signature = json.loads(json.dumps(base))
        tampered_signature["signature"] = "A" * 86
        with self.assertRaises(SignatureVerificationError):
            SignedEnvelopeV1.from_dict(tampered_signature).verify(
                keyring, datetime(2099, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
            )
        with self.assertRaisesRegex(PermissionError, "not currently valid"):
            SignedEnvelopeV1.from_dict(base).verify(
                keyring, datetime(2098, 1, 1, tzinfo=timezone.utc)
            )
        noncanonical_time = json.loads(json.dumps(base))
        noncanonical_time["not_before"] = "2099-01-01T00:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "canonical UTC"):
            SignedEnvelopeV1.from_dict(noncanonical_time)

    def test_issue_round_trip_is_immutable_strict_and_unknown_signers_fail(self) -> None:
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        signer = Ed25519Signer.generate()
        capital = CapitalEnvelopeV1(
            envelope_id=canonical_sha256(["envelope", 1]),
            mandate_id=canonical_sha256(["mandate", 1]),
            venue=Venue.DUMMY_KALSHI,
            account_hash=canonical_sha256(["account", "dummy"]),
            strategy_hashes=(canonical_sha256(["strategy", 1]),),
            passport_hashes=(canonical_sha256(["passport", 1]),),
            promotion_hashes=(canonical_sha256(["promotion", 1]),),
            authorized_instruments=("event_contract:TEST-MARKET",),
            authorized_mode=DesiredMode.LIVE,
            max_order_risk_cents=100,
            max_open_risk_cents=1000,
            max_correlated_risk_cents=300,
            max_daily_loss_cents=150,
            max_open_orders=5,
            fencing_generation=1,
            policy_epoch=1,
            not_before=now,
            expires_at=now + timedelta(seconds=60),
        )
        envelope = SignedEnvelopeV1.issue(
            capital,
            source_id="core",
            source_sequence=1,
            correlation_id="correlation-1",
            causation_id=None,
            nonce="nonce-1",
            not_before=now,
            expires_at=now + timedelta(seconds=60),
            signer=signer,
        )
        parsed = SignedEnvelopeV1.from_dict(envelope.to_dict())
        keyring = Ed25519Keyring()
        with self.assertRaisesRegex(SignatureVerificationError, "unknown signer"):
            parsed.verify(keyring, now + timedelta(seconds=1))
        keyring.register(signer.public_key_bytes)
        self.assertEqual(parsed.verify(keyring, now + timedelta(seconds=1)), capital)
        with self.assertRaisesRegex(ValueError, "unknown"):
            SignedEnvelopeV1.from_dict({**envelope.to_dict(), "unexpected": "field"})

    def test_contract_invariants_reject_unsorted_hashes_and_cost_mismatch(self) -> None:
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        kwargs = dict(
            envelope_id="e" * 64,
            mandate_id="d" * 64,
            venue=Venue.DOPEY_ROBINHOOD,
            account_hash="c" * 64,
            strategy_hashes=("b" * 64, "a" * 64),
            passport_hashes=("a" * 64,),
            promotion_hashes=("b" * 64,),
            authorized_instruments=("equity:TEST",),
            authorized_mode=DesiredMode.LIVE,
            max_order_risk_cents=100,
            max_open_risk_cents=1000,
            max_correlated_risk_cents=300,
            max_daily_loss_cents=150,
            max_open_orders=5,
            fencing_generation=1,
            policy_epoch=1,
            not_before=now,
            expires_at=now + timedelta(seconds=60),
        )
        with self.assertRaisesRegex(ValueError, "sorted"):
            CapitalEnvelopeV1(**kwargs)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            CapitalEnvelopeV1(
                **{
                    **kwargs,
                    "strategy_hashes": ("a" * 64, "b" * 64),
                    "passport_hashes": ("a" * 64, "b" * 64),
                    "promotion_hashes": ("a" * 64, "b" * 64),
                }
            )
        correlated = {
            **kwargs,
            "strategy_hashes": ("a" * 64,),
            "max_order_risk_cents": 301,
            "max_correlated_risk_cents": 300,
        }
        with self.assertRaisesRegex(ValueError, "correlated"):
            CapitalEnvelopeV1(**correlated)
        with self.assertRaisesRegex(ValueError, "does not reconcile"):
            OutcomeSettlementV1(
                settlement_id="settlement-1",
                intent_digest="a" * 64,
                lifecycle_digest="b" * 64,
                venue=Venue.DOPEY_ROBINHOOD,
                gross_pnl_cents=100,
                fees_cents=10,
                slippage_cents=5,
                model_cost_cents=1,
                net_pnl_cents=90,
                reconciliation_status=ReconciliationStatus.RECONCILED,
                settled_at=now,
            )

    def test_migration_manifest_is_secret_free_and_path_safe(self) -> None:
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        common = dict(
            migration_id="laptop-import-1",
            source_machine_hash="a" * 64,
            source_repository_hash="b" * 64,
            schema_versions=("dopey.v1",),
            file_hashes={"data/state.db": "c" * 64},
            row_counts={"trades": 10},
            created_at=now,
        )
        manifest = MigrationManifestV1(**common, contains_secrets=False)
        self.assertFalse(manifest.to_dict()["contains_secrets"])
        with self.assertRaisesRegex(ValueError, "secrets"):
            MigrationManifestV1(**common, contains_secrets=True)
        unsafe = {**common, "file_hashes": {"../secret": "c" * 64}}
        with self.assertRaisesRegex(ValueError, "unsafe"):
            MigrationManifestV1(**unsafe, contains_secrets=False)

    def test_cell_journal_anchor_round_trips_and_rejects_bad_heads(
        self,
    ) -> None:
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        identity = {
            "schema": CellJournalHeadAnchorV1.SCHEMA,
            "venue": Venue.DUMMY_KALSHI.value,
            "account_hash": "b" * 64,
            "journal_name": "capital-operational",
            "journal_schema": "dummy.sqlite-operational-journal.v1",
            "journal_stream_id": "c" * 64,
            "journal_sequence": 7,
            "journal_head_sha256": "d" * 64,
            "previous_anchor_body_digest": "e" * 64,
        }
        anchor = CellJournalHeadAnchorV1(
            anchor_id=canonical_sha256(identity),
            venue=Venue.DUMMY_KALSHI,
            account_hash="b" * 64,
            journal_name="capital-operational",
            journal_schema="dummy.sqlite-operational-journal.v1",
            journal_stream_id="c" * 64,
            journal_sequence=7,
            journal_head_sha256="d" * 64,
            previous_anchor_body_digest="e" * 64,
            anchored_at=now,
        )

        self.assertEqual(
            parse_contract(anchor.to_dict()),
            anchor,
        )
        with self.assertRaisesRegex(ValueError, "journal_sequence"):
            replace(anchor, journal_sequence=-1)
        with self.assertRaisesRegex(ValueError, "zero head"):
            replace(anchor, journal_sequence=0)
        with self.assertRaisesRegex(ValueError, "canonical journal head"):
            replace(anchor, anchor_id="f" * 64)

    def test_schema_ids_are_explicit_not_silently_aliased(self) -> None:
        raw = {
            "schema": "dumbmoney.alpha-passport.v1",
        }
        with self.assertRaisesRegex(ValueError, "unsupported"):
            parse_contract(raw)
        self.assertEqual(EvidenceClass.REALIZED.value, "REALIZED")

    def test_committed_json_schemas_load_strictly_without_duplicate_keys(self) -> None:
        schema_root = REPO_ROOT / "configs" / "dumbmoney" / "schemas"
        schema_paths = sorted(schema_root.glob("*.schema.json"))
        self.assertGreater(len(schema_paths), 0)
        for schema_path in schema_paths:
            with self.subTest(schema=schema_path.name):
                schema = load_strict_json(schema_path)
                self.assertEqual(
                    schema["$schema"],
                    "https://json-schema.org/draft/2020-12/schema",
                )

        resolver_schema = load_strict_json(
            schema_root / "cell-contract-resolution.v1.schema.json"
        )
        self.assertEqual(
            resolver_schema["properties"]["authority_state"],
            {"$ref": "#/$defs/authority_state"},
        )
        self.assertIn("authority_state", resolver_schema["$defs"])
        self.assertIn("authority_verdict", resolver_schema["$defs"])

    @unittest.skipUnless(DOOFUS_FIXTURE_ROOT.is_dir(), "sibling Doofus interop fixtures are unavailable")
    def test_doofus_fund_exports_parse_and_round_trip_byte_exactly(self) -> None:
        fixture_paths = sorted(DOOFUS_FIXTURE_ROOT.glob("*.json"))
        self.assertEqual(len(fixture_paths), 5)
        for fixture_path in fixture_paths:
            raw_bytes = fixture_path.read_bytes()
            raw = loads_strict_json(raw_bytes, str(fixture_path))
            parsed = parse_contract(raw)
            self.assertEqual(
                parsed.canonical_bytes() + b"\n",
                raw_bytes,
                fixture_path.name,
            )


if __name__ == "__main__":
    unittest.main()
