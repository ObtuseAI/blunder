from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from blunder.fund.canonical import canonical_json_bytes
from blunder.fund.cas import ContentAddressedStore, ContentIntegrityError
from blunder.fund.contracts import KillStateV1, SignedEnvelopeV1
from blunder.fund.crypto import Ed25519Keyring, Ed25519Signer
from blunder.fund.ledger import EventLedger, LedgerConflictError, LedgerIntegrityError


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class EventLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        self.clock = FixedClock(self.now)
        self.signer = Ed25519Signer.generate()
        self.keyring = Ed25519Keyring()
        self.keyring.register(self.signer.public_key_bytes)
        self.cas = ContentAddressedStore(self.root / "objects")
        self.ledger = EventLedger(
            self.root / "control.db",
            self.cas,
            self.keyring,
            clock=self.clock,
        )

    def tearDown(self) -> None:
        self.ledger.close()
        self.temporary.cleanup()

    def envelope(self, sequence: int, *, source: str = "operator", generation: int | None = None) -> SignedEnvelopeV1:
        body = KillStateV1(
            active=bool(sequence % 2),
            generation=generation or sequence,
            reason=f"test-generation-{generation or sequence}",
            changed_by="operator",
            policy_epoch=1,
            changed_at=self.now,
        )
        return SignedEnvelopeV1.issue(
            body,
            source_id=source,
            source_sequence=sequence,
            correlation_id="ledger-test",
            causation_id=None,
            nonce=f"{source}-nonce-{sequence}",
            not_before=self.now,
            expires_at=self.now + timedelta(minutes=5),
            signer=self.signer,
        )

    def test_append_is_deduplicated_and_hash_chained(self) -> None:
        first = self.ledger.append(self.envelope(1))
        duplicate = self.ledger.append(first.envelope)
        second = self.ledger.append(self.envelope(2))
        self.assertFalse(first.duplicate)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.global_sequence, first.global_sequence)
        self.assertEqual(second.previous_global_digest, first.event_digest)
        self.assertEqual(second.previous_source_digest, first.event_digest)
        verification = self.ledger.verify_chain()
        self.assertEqual(verification.event_count, 2)
        self.assertEqual(verification.global_head, second.event_digest)
        self.assertTrue(self.cas.has(first.payload_digest))

    def test_source_sequence_gaps_and_nonce_replay_fail_closed(self) -> None:
        self.ledger.append(self.envelope(1))
        with self.assertRaisesRegex(LedgerConflictError, "not contiguous"):
            self.ledger.append(self.envelope(3))
        replayed_nonce = self.envelope(1, source="other")
        raw = replayed_nonce.to_dict()
        raw["nonce"] = "operator-nonce-1"
        # Changing a signed field without resigning is rejected before uniqueness.
        with self.assertRaises(ValueError):
            self.ledger.append(SignedEnvelopeV1.from_dict(raw))

    def test_reopen_preserves_source_heads_and_accepts_next_sequence(self) -> None:
        first = self.ledger.append(self.envelope(1))
        self.ledger.close()
        self.ledger = EventLedger(
            self.root / "control.db",
            self.cas,
            self.keyring,
            clock=self.clock,
        )
        second = self.ledger.append(self.envelope(2))
        self.assertEqual(second.global_sequence, 2)
        self.assertEqual(second.previous_source_digest, first.event_digest)
        self.assertEqual(self.ledger.verify_chain().event_count, 2)

    def test_chain_tampering_and_cas_tampering_are_detected(self) -> None:
        record = self.ledger.append(self.envelope(1))
        connection = sqlite3.connect(self.root / "control.db")
        try:
            connection.execute(
                "UPDATE events SET previous_global_digest = ? WHERE event_id = ?",
                ("f" * 64, record.event_id),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(LedgerIntegrityError, "previous global"):
            self.ledger.verify_chain()

    def test_reopen_rejects_persisted_envelope_signature_tampering(self) -> None:
        record = self.ledger.append(self.envelope(1))
        tampered = record.envelope.to_dict()
        tampered["signature"] = "A" * 86
        connection = sqlite3.connect(self.root / "control.db")
        try:
            connection.execute(
                "UPDATE events SET envelope_json = ? WHERE event_id = ?",
                (canonical_json_bytes(tampered), record.event_id),
            )
            connection.commit()
        finally:
            connection.close()
        self.ledger.close()
        with self.assertRaisesRegex(
            LedgerIntegrityError,
            "authenticity verification",
        ):
            EventLedger(
                self.root / "control.db",
                self.cas,
                self.keyring,
                clock=self.clock,
            )

    def test_expired_envelope_is_never_appended(self) -> None:
        envelope = self.envelope(1)
        self.clock.now = self.now + timedelta(minutes=6)
        with self.assertRaisesRegex(PermissionError, "not currently valid"):
            self.ledger.append(envelope)
        self.assertEqual(self.ledger.head()[0], 0)

    def test_cas_rejects_changed_bytes_at_an_existing_address(self) -> None:
        digest = self.cas.put_bytes(b"immutable")
        self.cas.path_for(digest).write_bytes(b"tampered")
        with self.assertRaisesRegex(ContentIntegrityError, "does not match"):
            self.cas.put_bytes(b"immutable")
        with self.assertRaisesRegex(ContentIntegrityError, "failed digest"):
            self.cas.get_bytes(digest)


if __name__ == "__main__":
    unittest.main()
