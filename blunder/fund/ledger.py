"""Single-writer, append-only SQLite event ledger for DumbMoney."""

from __future__ import annotations

import queue
import sqlite3
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Mapping, cast

from blunder.fund.canonical import canonical_json_bytes, canonical_sha256, format_utc, loads_strict_json
from blunder.fund.cas import ContentAddressedStore
from blunder.fund.contracts import SignedEnvelopeV1
from blunder.fund.crypto import Ed25519Keyring


LEDGER_SCHEMA_VERSION = 1
ZERO_DIGEST = "0" * 64


class LedgerClosedError(RuntimeError):
    pass


class LedgerConflictError(ValueError):
    pass


class LedgerIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class EventRecord:
    global_sequence: int
    event_id: str
    source_id: str
    source_sequence: int
    signer_key_id: str
    nonce: str
    event_schema: str
    observed_at: datetime
    received_at: datetime
    correlation_id: str
    causation_id: str | None
    payload_digest: str
    previous_source_digest: str
    previous_global_digest: str
    event_digest: str
    envelope: SignedEnvelopeV1
    duplicate: bool = False


@dataclass(frozen=True)
class ChainVerification:
    event_count: int
    global_head: str
    source_heads: Mapping[str, str]


@dataclass
class _AppendRequest:
    envelope: SignedEnvelopeV1
    future: Future[EventRecord]


_STOP = object()


class EventLedger:
    """Own one writer connection and serialize all state-changing transactions."""

    def __init__(
        self,
        path: Path,
        cas: ContentAddressedStore,
        keyring: Ed25519Keyring,
        *,
        clock: Callable[[], datetime] | None = None,
        queue_capacity: int = 1024,
    ) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cas = cas
        self.keyring = keyring
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._queue: queue.Queue[object] = queue.Queue(maxsize=queue_capacity)
        self._closed = threading.Event()
        self._initialize()
        # Reopening is an authority boundary. Verify persisted signatures and
        # both chains before a writer thread can accept another event.
        self.verify_chain()
        self._thread = threading.Thread(target=self._writer_main, name="dumbmoney-ledger-writer", daemon=True)
        self._thread.start()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ledger_metadata (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    schema_version INTEGER NOT NULL,
                    last_global_sequence INTEGER NOT NULL,
                    global_chain_head TEXT NOT NULL
                ) STRICT;

                CREATE TABLE IF NOT EXISTS source_heads (
                    source_id TEXT PRIMARY KEY,
                    last_source_sequence INTEGER NOT NULL,
                    source_chain_head TEXT NOT NULL
                ) STRICT;

                CREATE TABLE IF NOT EXISTS events (
                    global_sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    source_id TEXT NOT NULL,
                    source_sequence INTEGER NOT NULL,
                    signer_key_id TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    event_schema TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    causation_id TEXT,
                    payload_digest TEXT NOT NULL,
                    previous_source_digest TEXT NOT NULL,
                    previous_global_digest TEXT NOT NULL,
                    event_digest TEXT NOT NULL UNIQUE,
                    envelope_json BLOB NOT NULL,
                    UNIQUE (source_id, source_sequence),
                    UNIQUE (signer_key_id, nonce)
                ) STRICT;

                CREATE INDEX IF NOT EXISTS idx_events_schema_sequence
                    ON events (event_schema, global_sequence);
                CREATE INDEX IF NOT EXISTS idx_events_correlation_sequence
                    ON events (correlation_id, global_sequence);
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO ledger_metadata
                    (singleton, schema_version, last_global_sequence, global_chain_head)
                VALUES (1, ?, 0, ?)
                """,
                (LEDGER_SCHEMA_VERSION, ZERO_DIGEST),
            )
            row = connection.execute(
                "SELECT schema_version FROM ledger_metadata WHERE singleton = 1"
            ).fetchone()
            if row is None or row["schema_version"] != LEDGER_SCHEMA_VERSION:
                raise LedgerIntegrityError("unsupported DumbMoney ledger schema version")
        finally:
            connection.close()

    def append(self, envelope: SignedEnvelopeV1, timeout: float = 10.0) -> EventRecord:
        if self._closed.is_set():
            raise LedgerClosedError("event ledger is closed")
        future: Future[EventRecord] = Future()
        try:
            self._queue.put(_AppendRequest(envelope, future), timeout=timeout)
        except queue.Full as exc:
            raise TimeoutError("event ledger writer queue is full") from exc
        return future.result(timeout=timeout)

    def _writer_main(self) -> None:
        connection = self._connect()
        connection.execute("PRAGMA synchronous = FULL")
        try:
            while True:
                request = self._queue.get()
                try:
                    if request is _STOP:
                        return
                    append_request = cast(_AppendRequest, request)
                    if append_request.future.cancelled():
                        continue
                    try:
                        record = self._append_transaction(connection, append_request.envelope)
                    except BaseException as exc:
                        append_request.future.set_exception(exc)
                    else:
                        append_request.future.set_result(record)
                finally:
                    self._queue.task_done()
        finally:
            connection.close()

    def _append_transaction(self, connection: sqlite3.Connection, envelope: SignedEnvelopeV1) -> EventRecord:
        received_at = self.clock()
        contract = envelope.verify(self.keyring, received_at)
        payload_digest = self.cas.put_json(envelope.body)
        if payload_digest != envelope.body_digest:
            raise LedgerIntegrityError("CAS digest differs from signed body digest")
        encoded_envelope = canonical_json_bytes(envelope.to_dict())
        connection.execute("BEGIN IMMEDIATE")
        try:
            duplicate = connection.execute(
                "SELECT * FROM events WHERE event_id = ?",
                (envelope.event_id,),
            ).fetchone()
            if duplicate is not None:
                if bytes(duplicate["envelope_json"]) != encoded_envelope:
                    raise LedgerConflictError("event ID collision or conflicting duplicate")
                connection.execute("COMMIT")
                return self._record_from_row(duplicate, duplicate=True)

            metadata = connection.execute(
                "SELECT last_global_sequence, global_chain_head FROM ledger_metadata WHERE singleton = 1"
            ).fetchone()
            if metadata is None:
                raise LedgerIntegrityError("ledger metadata row is missing")
            source = connection.execute(
                "SELECT last_source_sequence, source_chain_head FROM source_heads WHERE source_id = ?",
                (envelope.source_id,),
            ).fetchone()
            expected_source_sequence = 1 if source is None else int(source["last_source_sequence"]) + 1
            if envelope.source_sequence != expected_source_sequence:
                raise LedgerConflictError(
                    f"source sequence is not contiguous: source={envelope.source_id}; "
                    f"expected={expected_source_sequence}; observed={envelope.source_sequence}"
                )
            global_sequence = int(metadata["last_global_sequence"]) + 1
            previous_global_digest = str(metadata["global_chain_head"])
            previous_source_digest = ZERO_DIGEST if source is None else str(source["source_chain_head"])
            observed_at = max(received_at.astimezone(timezone.utc), envelope.not_before)
            event_core = {
                "global_sequence": global_sequence,
                "event_id": envelope.event_id,
                "source_id": envelope.source_id,
                "source_sequence": envelope.source_sequence,
                "signer_key_id": envelope.signer_key_id,
                "nonce": envelope.nonce,
                "event_schema": contract.SCHEMA,
                "observed_at": format_utc(observed_at),
                "received_at": format_utc(received_at),
                "correlation_id": envelope.correlation_id,
                "causation_id": envelope.causation_id,
                "payload_digest": payload_digest,
                "previous_source_digest": previous_source_digest,
                "previous_global_digest": previous_global_digest,
            }
            event_digest = canonical_sha256(event_core)
            connection.execute(
                """
                INSERT INTO events (
                    global_sequence, event_id, source_id, source_sequence, signer_key_id,
                    nonce, event_schema, observed_at, received_at, correlation_id,
                    causation_id, payload_digest, previous_source_digest,
                    previous_global_digest, event_digest, envelope_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    global_sequence,
                    envelope.event_id,
                    envelope.source_id,
                    envelope.source_sequence,
                    envelope.signer_key_id,
                    envelope.nonce,
                    contract.SCHEMA,
                    format_utc(observed_at),
                    format_utc(received_at),
                    envelope.correlation_id,
                    envelope.causation_id,
                    payload_digest,
                    previous_source_digest,
                    previous_global_digest,
                    event_digest,
                    encoded_envelope,
                ),
            )
            connection.execute(
                """
                INSERT INTO source_heads (source_id, last_source_sequence, source_chain_head)
                VALUES (?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    last_source_sequence = excluded.last_source_sequence,
                    source_chain_head = excluded.source_chain_head
                """,
                (envelope.source_id, envelope.source_sequence, event_digest),
            )
            connection.execute(
                """
                UPDATE ledger_metadata
                SET last_global_sequence = ?, global_chain_head = ?
                WHERE singleton = 1
                """,
                (global_sequence, event_digest),
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return EventRecord(
            global_sequence=global_sequence,
            event_id=envelope.event_id,
            source_id=envelope.source_id,
            source_sequence=envelope.source_sequence,
            signer_key_id=envelope.signer_key_id,
            nonce=envelope.nonce,
            event_schema=contract.SCHEMA,
            observed_at=observed_at,
            received_at=received_at.astimezone(timezone.utc),
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            payload_digest=payload_digest,
            previous_source_digest=previous_source_digest,
            previous_global_digest=previous_global_digest,
            event_digest=event_digest,
            envelope=envelope,
        )

    @staticmethod
    def _record_from_row(row: sqlite3.Row, *, duplicate: bool = False) -> EventRecord:
        raw = loads_strict_json(bytes(row["envelope_json"]), "ledger envelope")
        envelope = SignedEnvelopeV1.from_dict(raw)
        return EventRecord(
            global_sequence=int(row["global_sequence"]),
            event_id=str(row["event_id"]),
            source_id=str(row["source_id"]),
            source_sequence=int(row["source_sequence"]),
            signer_key_id=str(row["signer_key_id"]),
            nonce=str(row["nonce"]),
            event_schema=str(row["event_schema"]),
            observed_at=datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00")),
            received_at=datetime.fromisoformat(str(row["received_at"]).replace("Z", "+00:00")),
            correlation_id=str(row["correlation_id"]),
            causation_id=None if row["causation_id"] is None else str(row["causation_id"]),
            payload_digest=str(row["payload_digest"]),
            previous_source_digest=str(row["previous_source_digest"]),
            previous_global_digest=str(row["previous_global_digest"]),
            event_digest=str(row["event_digest"]),
            envelope=envelope,
            duplicate=duplicate,
        )

    def iter_events(
        self,
        *,
        after_sequence: int = 0,
        schema: str | None = None,
        limit: int | None = None,
    ) -> Iterator[EventRecord]:
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")
        connection = self._connect()
        try:
            if schema is None:
                sql = "SELECT * FROM events WHERE global_sequence > ? ORDER BY global_sequence"
                parameters: tuple[object, ...] = (after_sequence,)
            else:
                sql = (
                    "SELECT * FROM events WHERE global_sequence > ? "
                    "AND event_schema = ? ORDER BY global_sequence"
                )
                parameters = (after_sequence, schema)
            if limit is not None:
                sql += " LIMIT ?"
                parameters = (*parameters, limit)
            rows = connection.execute(sql, parameters).fetchall()
        finally:
            connection.close()
        for row in rows:
            yield self._record_from_row(row)

    def cursor_digest(self, global_sequence: int) -> str:
        """Resolve an exact global cursor to its event digest."""

        if isinstance(global_sequence, bool) or not isinstance(global_sequence, int) or global_sequence < 0:
            raise ValueError("global_sequence must be a non-negative integer")
        if global_sequence == 0:
            return ZERO_DIGEST
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT event_digest FROM events WHERE global_sequence = ?",
                (global_sequence,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            head_sequence, _head_digest = self.head()
            if global_sequence > head_sequence:
                raise LedgerConflictError("cursor sequence is ahead of the ledger head")
            raise LedgerIntegrityError("cursor sequence is missing from the append-only ledger")
        return str(row["event_digest"])

    def last_source_sequence(self, source_id: str) -> int:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT last_source_sequence FROM source_heads WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            return 0 if row is None else int(row["last_source_sequence"])
        finally:
            connection.close()

    def head(self) -> tuple[int, str]:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT last_global_sequence, global_chain_head FROM ledger_metadata WHERE singleton = 1"
            ).fetchone()
            if row is None:
                raise LedgerIntegrityError("ledger metadata row is missing")
            return int(row["last_global_sequence"]), str(row["global_chain_head"])
        finally:
            connection.close()

    def verify_chain(self) -> ChainVerification:
        previous_global = ZERO_DIGEST
        source_heads: dict[str, str] = {}
        source_sequences: dict[str, int] = {}
        count = 0
        for record in self.iter_events():
            try:
                contract = record.envelope.verify_authenticity(self.keyring)
            except (TypeError, ValueError) as exc:
                raise LedgerIntegrityError(
                    "persisted ledger envelope failed authenticity verification"
                ) from exc
            envelope = record.envelope
            if (
                record.event_id != envelope.event_id
                or record.source_id != envelope.source_id
                or record.source_sequence != envelope.source_sequence
                or record.signer_key_id != envelope.signer_key_id
                or record.nonce != envelope.nonce
                or record.correlation_id != envelope.correlation_id
                or record.causation_id != envelope.causation_id
                or record.payload_digest != envelope.body_digest
                or record.event_schema != contract.SCHEMA
            ):
                raise LedgerIntegrityError(
                    "persisted ledger row does not match its signed envelope"
                )
            expected_observed_at = max(
                record.received_at.astimezone(timezone.utc),
                envelope.not_before,
            )
            if record.observed_at != expected_observed_at:
                raise LedgerIntegrityError(
                    "persisted ledger observation time is not derivable"
                )
            expected_source_sequence = source_sequences.get(record.source_id, 0) + 1
            if record.source_sequence != expected_source_sequence:
                raise LedgerIntegrityError("source sequence chain is not contiguous")
            expected_source_head = source_heads.get(record.source_id, ZERO_DIGEST)
            if record.previous_source_digest != expected_source_head:
                raise LedgerIntegrityError("previous source digest mismatch")
            if record.previous_global_digest != previous_global:
                raise LedgerIntegrityError("previous global digest mismatch")
            core = {
                "global_sequence": record.global_sequence,
                "event_id": record.event_id,
                "source_id": record.source_id,
                "source_sequence": record.source_sequence,
                "signer_key_id": record.signer_key_id,
                "nonce": record.nonce,
                "event_schema": record.event_schema,
                "observed_at": format_utc(record.observed_at),
                "received_at": format_utc(record.received_at),
                "correlation_id": record.correlation_id,
                "causation_id": record.causation_id,
                "payload_digest": record.payload_digest,
                "previous_source_digest": record.previous_source_digest,
                "previous_global_digest": record.previous_global_digest,
            }
            expected_digest = canonical_sha256(core)
            if record.event_digest != expected_digest:
                raise LedgerIntegrityError("event digest mismatch")
            if self.cas.get_bytes(record.payload_digest) != canonical_json_bytes(record.envelope.body):
                raise LedgerIntegrityError("ledger payload differs from CAS")
            previous_global = record.event_digest
            source_heads[record.source_id] = record.event_digest
            source_sequences[record.source_id] = record.source_sequence
            count += 1
        metadata_sequence, metadata_head = self.head()
        if metadata_sequence != count or metadata_head != previous_global:
            raise LedgerIntegrityError("ledger metadata head does not match replayed chain")
        return ChainVerification(count, previous_global, dict(source_heads))

    def close(self, timeout: float = 10.0) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._queue.put(_STOP, timeout=timeout)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            raise TimeoutError("event ledger writer did not stop")

    def __enter__(self) -> "EventLedger":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()
