from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from blunder.api.contracts import (
    TaskEventV1,
    TaskStageV1,
    TaskStateV1,
    canonical_sha256,
    public_value,
    require_digest,
    require_identifier,
    require_integer,
    require_object,
    require_text,
    strict_json_loads,
)


EVENT_INDEX_SCHEMA = "obtuse.blunder.task-event-index.v1"
EVENT_SCHEMA = "obtuse.blunder.task-event.v1"
MAX_EVENTS = 100_000


class TaskEventLedger:
    def __init__(self, repository_root: Path) -> None:
        self._repository_root = repository_root.resolve()
        self._root = self._repository_root / "artifacts" / "obtuse" / "blunder-vnext-v1" / "events"
        self._record_root = self._root / "records"
        self._index_path = self._root / "index.json"
        self._lock = threading.RLock()

    def _empty_index(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schema": EVENT_INDEX_SCHEMA,
            "sequence": 0,
            "headDigest": None,
            "records": [],
        }
        return value | {"indexDigest": canonical_sha256(value)}

    def _atomic_write(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        with temporary.open("xb") as handle:
            handle.write((json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)

    def _validate_index(self, value: Mapping[str, object]) -> dict[str, object]:
        expected = {"schema", "sequence", "headDigest", "records", "indexDigest"}
        if set(value) != expected or value.get("schema") != EVENT_INDEX_SCHEMA:
            raise ValueError("Task event index fields or schema are invalid.")
        supplied_digest = require_digest(value.get("indexDigest"), "Task event index digest")
        payload = {key: item for key, item in value.items() if key != "indexDigest"}
        if canonical_sha256(payload) != supplied_digest:
            raise ValueError("Task event index digest does not match its contents.")
        sequence = require_integer(value.get("sequence"), "Task event index sequence", 0, MAX_EVENTS)
        head = value.get("headDigest")
        if head is not None:
            require_digest(head, "Task event index headDigest")
        records = value.get("records")
        if not isinstance(records, list) or len(records) != sequence:
            raise ValueError("Task event index record count does not match its sequence.")
        previous: str | None = None
        for expected_sequence, raw_record in enumerate(records, 1):
            record = require_object(raw_record, f"Task event index record {expected_sequence}")
            if set(record) != {"sequence", "digest", "path"}:
                raise ValueError(f"Task event index record {expected_sequence} fields are invalid.")
            if require_integer(record["sequence"], "Task event record sequence", 1, MAX_EVENTS) != expected_sequence:
                raise ValueError("Task event index sequences are not contiguous.")
            digest = require_digest(record["digest"], "Task event record digest")
            relative = require_text(record["path"], "Task event record path", 256)
            path = (self._root / relative).resolve()
            if not path.is_relative_to(self._record_root.resolve()) or not path.is_file():
                raise ValueError(f"Task event record is missing or escapes the ledger: {relative}")
            event = self._read_event(path, digest)
            if event.previous_event_digest != previous:
                raise ValueError(f"Task event chain is broken at sequence {expected_sequence}.")
            previous = digest
        if previous != head:
            raise ValueError("Task event index head digest does not match its final record.")
        return dict(value)

    def _load_index(self) -> dict[str, object]:
        if not self._index_path.is_file():
            return self._empty_index()
        value = strict_json_loads(self._index_path.read_text(encoding="utf-8"), "Task event index")
        return self._validate_index(require_object(value, "Task event index"))

    def _read_event(self, path: Path, expected_digest: str) -> TaskEventV1:
        raw = require_object(strict_json_loads(path.read_text(encoding="utf-8"), "Task event"), "Task event")
        expected = {
            "schema",
            "eventId",
            "taskId",
            "projectId",
            "sequence",
            "kind",
            "stage",
            "state",
            "causalIdentity",
            "inputDigests",
            "outputDigests",
            "evidenceReferences",
            "resourceCharge",
            "payload",
            "createdAt",
            "previousEventDigest",
            "eventDigest",
        }
        if set(raw) != expected or raw["schema"] != EVENT_SCHEMA:
            raise ValueError(f"Task event fields or schema are invalid: {path}")
        supplied = require_digest(raw["eventDigest"], "Task event digest")
        if supplied != expected_digest:
            raise ValueError(f"Task event identity differs from its index: {path}")
        digest_payload = {key: item for key, item in raw.items() if key != "eventDigest"}
        if canonical_sha256(digest_payload) != supplied:
            raise ValueError(f"Task event digest does not match its contents: {path}")
        try:
            stage = TaskStageV1(str(raw["stage"]))
            state = TaskStateV1(str(raw["state"]))
        except ValueError as error:
            raise ValueError(f"Task event stage or state is invalid: {path}") from error
        previous = raw["previousEventDigest"]
        if previous is not None:
            previous = require_digest(previous, "Task event previousEventDigest")
        input_digests = self._digest_tuple(raw["inputDigests"], "Task event inputDigests")
        output_digests = self._digest_tuple(raw["outputDigests"], "Task event outputDigests")
        evidence = self._text_tuple(raw["evidenceReferences"], "Task event evidenceReferences")
        return TaskEventV1(
            EVENT_SCHEMA,
            require_identifier(raw["eventId"], "Task event id"),
            require_identifier(raw["taskId"], "Task event taskId"),
            require_identifier(raw["projectId"], "Task event projectId"),
            require_integer(raw["sequence"], "Task event sequence", 1, MAX_EVENTS),
            require_text(raw["kind"], "Task event kind", 128),
            stage,
            state,
            require_digest(raw["causalIdentity"], "Task event causalIdentity"),
            input_digests,
            output_digests,
            evidence,
            require_object(raw["resourceCharge"], "Task event resourceCharge"),
            require_object(raw["payload"], "Task event payload"),
            require_text(raw["createdAt"], "Task event createdAt", 100),
            previous,
            supplied,
        )

    def _digest_tuple(self, value: object, context: str) -> tuple[str, ...]:
        if not isinstance(value, list) or len(value) > 128:
            raise ValueError(f"{context} must be a bounded list.")
        return tuple(require_digest(item, f"{context} item") for item in value)

    def _text_tuple(self, value: object, context: str) -> tuple[str, ...]:
        if not isinstance(value, list) or len(value) > 128:
            raise ValueError(f"{context} must be a bounded list.")
        return tuple(require_text(item, f"{context} item", 512) for item in value)

    def append(
        self,
        task_id: str,
        project_id: str,
        kind: str,
        stage: TaskStageV1,
        state: TaskStateV1,
        input_digests: Sequence[str],
        output_digests: Sequence[str],
        evidence_references: Sequence[str],
        resource_charge: Mapping[str, int | float | str],
        payload: Mapping[str, object],
        observed_at: datetime,
    ) -> TaskEventV1:
        with self._lock:
            index = self._load_index()
            sequence = int(index["sequence"]) + 1
            if sequence > MAX_EVENTS:
                raise ValueError("Task event ledger capacity is exhausted; operator retention review is required.")
            created_at = observed_at.astimezone(timezone.utc).isoformat()
            event_id = f"evt-{sequence:08d}-{uuid.uuid4().hex[:12]}"
            causal_identity = canonical_sha256(
                {
                    "eventId": event_id,
                    "taskId": task_id,
                    "projectId": project_id,
                    "sequence": sequence,
                    "kind": kind,
                    "createdAt": created_at,
                }
            )
            event_without_digest: dict[str, object] = {
                "schema": EVENT_SCHEMA,
                "eventId": require_identifier(event_id, "Task event id"),
                "taskId": require_identifier(task_id, "Task event taskId"),
                "projectId": require_identifier(project_id, "Task event projectId"),
                "sequence": sequence,
                "kind": require_text(kind, "Task event kind", 128),
                "stage": stage.value,
                "state": state.value,
                "causalIdentity": causal_identity,
                "inputDigests": [require_digest(item, "Task event input digest") for item in input_digests],
                "outputDigests": [require_digest(item, "Task event output digest") for item in output_digests],
                "evidenceReferences": [require_text(item, "Task event evidence reference", 512) for item in evidence_references],
                "resourceCharge": dict(resource_charge),
                "payload": dict(payload),
                "createdAt": created_at,
                "previousEventDigest": index["headDigest"],
            }
            digest = canonical_sha256(event_without_digest)
            event_value = event_without_digest | {"eventDigest": digest}
            relative = f"records/{sequence:08d}-{digest}.json"
            event_path = self._root / relative
            self._atomic_write(event_path, event_value)
            records = list(index["records"])
            records.append({"sequence": sequence, "digest": digest, "path": relative})
            next_index: dict[str, object] = {
                "schema": EVENT_INDEX_SCHEMA,
                "sequence": sequence,
                "headDigest": digest,
                "records": records,
            }
            next_index["indexDigest"] = canonical_sha256(next_index)
            self._atomic_write(self._index_path, next_index)
            return self._read_event(event_path, digest)

    def events_after(self, cursor: int, task_id: str | None, limit: int) -> tuple[TaskEventV1, ...]:
        with self._lock:
            index = self._load_index()
            head = int(index["sequence"])
            if cursor < 0 or cursor > head:
                raise ValueError(f"Task event cursor {cursor} is stale or ahead of ledger head {head}.")
            if limit < 1 or limit > 500:
                raise ValueError("Task event limit must be between 1 and 500.")
            selected: list[TaskEventV1] = []
            for raw_record in list(index["records"])[cursor:]:
                record = require_object(raw_record, "Task event index record")
                path = self._root / str(record["path"])
                event = self._read_event(path, str(record["digest"]))
                if task_id is None or event.task_id == task_id:
                    selected.append(event)
                if len(selected) >= limit:
                    break
            return tuple(selected)

    def public_events_after(self, cursor: int, task_id: str | None, limit: int) -> dict[str, object]:
        events = self.events_after(cursor, task_id, limit)
        head = int(self._load_index()["sequence"])
        return {
            "schema": "obtuse.blunder.task-event-page.v1",
            "cursor": cursor,
            "nextCursor": events[-1].sequence if events else cursor,
            "head": head,
            "events": [public_value(event) for event in events],
        }

    def recovery_report(self) -> dict[str, object]:
        with self._lock:
            index = self._load_index()
            referenced = {
                str((self._root / str(require_object(item, "Task event index record")["path"])).resolve())
                for item in list(index["records"])
            }
            orphans = [
                path.relative_to(self._root).as_posix()
                for path in sorted(self._record_root.glob("*.json"))
                if str(path.resolve()) not in referenced
            ]
            interrupted = [path.relative_to(self._root).as_posix() for path in sorted(self._root.rglob("*.tmp"))]
            return {
                "schema": "obtuse.blunder.task-event-recovery.v1",
                "status": "REVIEW_REQUIRED" if orphans or interrupted else "CLEAN",
                "sequence": index["sequence"],
                "headDigest": index["headDigest"],
                "orphanRecords": orphans,
                "interruptedWrites": interrupted,
                "automaticRecoveryPerformed": False,
            }
