"""Durable, authority-closed runtime storage for Blunder CCOS evidence."""

from __future__ import annotations

import ctypes
import json
import math
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from blunder.cognitive_ecology import canonical_digest, require_digest


CONFIG_SCHEMA = "obtuse.blunder.ccos-runtime-config.v1"
INDEX_SCHEMA = "obtuse.blunder.ccos-runtime-index.v1"
RECORD_SCHEMA = "obtuse.blunder.ccos-runtime-record.v1"
STATE_SCHEMA = "obtuse.blunder.ccos-runtime-state.v1"
RECOVERY_CONFIRMATION = "RECOVER_CCOS_LAST_KNOWN_GOOD"
RECORD_KINDS: tuple[str, ...] = (
    "genome",
    "evaluation",
    "memory",
    "lineage",
    "observatory",
    "quarantine",
    "recovery",
    "canary",
)
PROHIBITED_FIELDS: frozenset[str] = frozenset(
    {
        "activation",
        "approval",
        "authority",
        "chainofthought",
        "credentials",
        "password",
        "privatememory",
        "rawreasoning",
        "reasoningtokens",
        "secret",
        "token",
    }
)
_PROCESS_WRITE_LOCK = threading.RLock()
MAXIMUM_PAYLOAD_DEPTH = 32
MAXIMUM_PAYLOAD_NODES = 10_000


@dataclass(frozen=True)
class CCOSRuntimeConfig:
    schema: str
    artifact_root: Path
    maximum_record_bytes: int
    maximum_records: int
    lock_timeout_seconds: float
    stale_lock_seconds: float
    retention_days: Mapping[str, int]
    provider_calls_allowed: bool
    automatic_activation_allowed: bool
    federation_allowed: bool
    meta_evolution_allowed: bool


def _require_object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{context} must be a JSON object.")
    return dict(value)


def _require_number(value: object, context: str, minimum: float) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < minimum
    ):
        raise ValueError(f"{context} must be a number greater than or equal to {minimum}.")
    return float(value)


def _require_integer(value: object, context: str, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{context} must be an integer greater than or equal to {minimum}.")
    return value


def _require_false(value: object, context: str) -> bool:
    if value is not False:
        raise PermissionError(f"{context} must remain false in the CCOS runtime configuration.")
    return False


def _validate_runtime_config_instance(config: CCOSRuntimeConfig) -> None:
    if config.schema != CONFIG_SCHEMA:
        raise ValueError(f"CCOS runtime configuration schema must be {CONFIG_SCHEMA}.")
    if any(
        (
            config.provider_calls_allowed,
            config.automatic_activation_allowed,
            config.federation_allowed,
            config.meta_evolution_allowed,
        )
    ):
        raise PermissionError("CCOS runtime effect authorities must remain false.")
    if config.maximum_record_bytes < 1024 or config.maximum_record_bytes > 16_777_216:
        raise ValueError("CCOS maximum record bytes are outside the supported range.")
    if config.maximum_records < 8 or config.maximum_records > 1_000_000:
        raise ValueError("CCOS maximum records are outside the supported range.")
    if config.lock_timeout_seconds < 0.1 or config.lock_timeout_seconds > 60.0:
        raise ValueError("CCOS lock timeout is outside the supported range.")
    if config.stale_lock_seconds < config.lock_timeout_seconds or config.stale_lock_seconds > 86_400.0:
        raise ValueError("CCOS stale lock threshold is outside the supported range.")
    if set(config.retention_days) != set(RECORD_KINDS):
        raise ValueError("CCOS retention policy must define every and only supported record kind.")
    if any(not isinstance(days, int) or isinstance(days, bool) or days < 1 for days in config.retention_days.values()):
        raise ValueError("CCOS retention policy days must be positive integers.")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON object contains a duplicate key: {key}")
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> object:
    raise ValueError(f"JSON contains a non-finite number: {value}")


def _parse_json(text: str, context: str) -> object:
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_json,
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"{context} contains invalid JSON: {error}") from error


def load_runtime_config(repository_root: Path, config_path: Path) -> CCOSRuntimeConfig:
    """Load one explicit fail-closed runtime configuration."""

    root = repository_root.resolve()
    resolved_config = config_path.resolve()
    if not resolved_config.is_file():
        raise FileNotFoundError(f"CCOS runtime configuration is missing: {resolved_config}")
    if not resolved_config.is_relative_to(root):
        raise ValueError("CCOS runtime configuration must be inside the repository root.")
    value = _require_object(
        _parse_json(resolved_config.read_text(encoding="utf-8"), "CCOS runtime configuration"),
        "CCOS runtime configuration",
    )
    if value.get("schema") != CONFIG_SCHEMA:
        raise ValueError(f"CCOS runtime configuration schema must be {CONFIG_SCHEMA}.")
    expected_fields = {
        "schema",
        "artifactRoot",
        "maximumRecordBytes",
        "maximumRecords",
        "lockTimeoutSeconds",
        "staleLockSeconds",
        "retentionDays",
        "authority",
    }
    if set(value) != expected_fields:
        raise ValueError("CCOS runtime configuration has unexpected or missing fields.")
    raw_artifact_root = value.get("artifactRoot")
    if not isinstance(raw_artifact_root, str) or not raw_artifact_root.strip():
        raise ValueError("CCOS artifactRoot must be a non-empty relative path.")
    relative_artifact_root = Path(raw_artifact_root)
    if relative_artifact_root.is_absolute():
        raise ValueError("CCOS artifactRoot must be relative to the repository root.")
    if not relative_artifact_root.parts or relative_artifact_root.parts[0].casefold() != "artifacts":
        raise ValueError("CCOS artifactRoot must be contained under the repository artifacts directory.")
    artifact_root = (root / relative_artifact_root).resolve()
    if not artifact_root.is_relative_to(root):
        raise ValueError("CCOS artifactRoot escapes the repository root.")
    maximum_record_bytes = _require_integer(value.get("maximumRecordBytes"), "maximumRecordBytes", 1024)
    if maximum_record_bytes > 16_777_216:
        raise ValueError("maximumRecordBytes must not exceed 16777216 bytes.")
    maximum_records = _require_integer(value.get("maximumRecords"), "maximumRecords", 8)
    if maximum_records > 1_000_000:
        raise ValueError("maximumRecords must not exceed 1000000.")
    lock_timeout_seconds = _require_number(value.get("lockTimeoutSeconds"), "lockTimeoutSeconds", 0.1)
    stale_lock_seconds = _require_number(value.get("staleLockSeconds"), "staleLockSeconds", lock_timeout_seconds)
    if lock_timeout_seconds > 60.0 or stale_lock_seconds > 86_400.0:
        raise ValueError("CCOS lock timeout or stale threshold exceeds its safe maximum.")
    raw_retention = _require_object(value.get("retentionDays"), "retentionDays")
    if set(raw_retention) != set(RECORD_KINDS):
        raise ValueError("retentionDays must define every and only supported CCOS record kind.")
    retention_days: dict[str, int] = {}
    for kind, raw_days in raw_retention.items():
        retention_days[kind] = _require_integer(raw_days, f"retentionDays.{kind}", 1)
    authority = _require_object(value.get("authority"), "authority")
    expected_authority_fields = {
        "providerCallsAllowed",
        "automaticActivationAllowed",
        "federationAllowed",
        "metaEvolutionAllowed",
    }
    if set(authority) != expected_authority_fields:
        raise ValueError("CCOS authority configuration has unexpected or missing fields.")
    return CCOSRuntimeConfig(
        schema=CONFIG_SCHEMA,
        artifact_root=artifact_root,
        maximum_record_bytes=maximum_record_bytes,
        maximum_records=maximum_records,
        lock_timeout_seconds=lock_timeout_seconds,
        stale_lock_seconds=stale_lock_seconds,
        retention_days=retention_days,
        provider_calls_allowed=_require_false(authority["providerCallsAllowed"], "providerCallsAllowed"),
        automatic_activation_allowed=_require_false(
            authority["automaticActivationAllowed"], "automaticActivationAllowed"
        ),
        federation_allowed=_require_false(authority["federationAllowed"], "federationAllowed"),
        meta_evolution_allowed=_require_false(authority["metaEvolutionAllowed"], "metaEvolutionAllowed"),
    )


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(_canonical_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_object(path: Path, context: str) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as error:
        raise ValueError(f"{context} is not valid UTF-8: {path}: {error}") from error
    value = _parse_json(text, f"{context}: {path}")
    return _require_object(value, context)


def load_runtime_payload(path: Path) -> dict[str, object]:
    """Load one strict JSON evidence object for an operator append."""

    value = _read_object(path.resolve(), "CCOS evidence input")
    _validate_payload(value, "payload")
    return value


def _empty_index() -> dict[str, object]:
    core: dict[str, object] = {
        "schema": INDEX_SCHEMA,
        "generation": 0,
        "headDigest": None,
        "records": [],
    }
    return core | {"indexDigest": canonical_digest(core)}


def _validate_record(value: Mapping[str, object], expected_digest: str) -> dict[str, object]:
    record = dict(value)
    expected_fields = {
        "schema",
        "sequence",
        "previousDigest",
        "kind",
        "recordedAt",
        "payloadDigest",
        "payload",
        "authorityGranted",
        "providerCalls",
        "automaticActivation",
        "recordDigest",
    }
    if set(record) != expected_fields:
        raise ValueError("CCOS record has unexpected or missing fields.")
    claimed_digest = require_digest(str(record.get("recordDigest") or ""), "CCOS record")
    if claimed_digest != expected_digest:
        raise ValueError(f"CCOS record filename identity mismatch: expected {expected_digest}, observed {claimed_digest}.")
    core = {key: item for key, item in record.items() if key != "recordDigest"}
    if record.get("schema") != RECORD_SCHEMA or canonical_digest(core) != claimed_digest:
        raise ValueError(f"CCOS record digest mismatch: {expected_digest}.")
    payload = _require_object(record.get("payload"), "CCOS record payload")
    _validate_payload(payload, "payload")
    if canonical_digest(payload) != record.get("payloadDigest"):
        raise ValueError(f"CCOS record payload digest mismatch: {expected_digest}.")
    if record.get("kind") not in RECORD_KINDS:
        raise ValueError(f"CCOS record kind is unsupported: {record.get('kind')}.")
    if record.get("authorityGranted") is not False or record.get("automaticActivation") is not False:
        raise PermissionError("CCOS record authority fields must remain false.")
    if record.get("providerCalls") != 0:
        raise PermissionError("CCOS durable runtime records must remain provider-free.")
    raw_sequence = record.get("sequence")
    if not isinstance(raw_sequence, int) or isinstance(raw_sequence, bool) or raw_sequence < 0:
        raise ValueError("CCOS record sequence must be a non-negative integer.")
    recorded_at = datetime.fromisoformat(str(record.get("recordedAt") or "").replace("Z", "+00:00"))
    if recorded_at.tzinfo is None:
        raise ValueError("CCOS record time must include a timezone.")
    return record


def _validate_index(config: CCOSRuntimeConfig, value: Mapping[str, object]) -> dict[str, object]:
    index = dict(value)
    if set(index) != {"schema", "generation", "headDigest", "records", "indexDigest"}:
        raise ValueError("CCOS runtime index has unexpected or missing fields.")
    claimed_digest = require_digest(str(index.get("indexDigest") or ""), "CCOS index")
    core = {key: item for key, item in index.items() if key != "indexDigest"}
    if index.get("schema") != INDEX_SCHEMA or canonical_digest(core) != claimed_digest:
        raise ValueError("CCOS runtime index digest mismatch.")
    records = index.get("records")
    if not isinstance(records, list) or any(not isinstance(item, str) for item in records):
        raise TypeError("CCOS runtime index records must be a digest list.")
    if index.get("generation") != len(records):
        raise ValueError("CCOS runtime index generation does not match its record count.")
    expected_head = records[-1] if records else None
    if index.get("headDigest") != expected_head:
        raise ValueError("CCOS runtime index head does not match its record sequence.")
    previous_digest: str | None = None
    for sequence, raw_digest in enumerate(records):
        digest = require_digest(raw_digest, "CCOS index record")
        record_path = config.artifact_root / "records" / f"{digest}.json"
        if not record_path.is_file():
            raise FileNotFoundError(f"CCOS indexed record is missing: {record_path}")
        record = _validate_record(_read_object(record_path, "CCOS record"), digest)
        if record.get("sequence") != sequence or record.get("previousDigest") != previous_digest:
            raise ValueError(f"CCOS record chain is invalid at sequence {sequence}.")
        previous_digest = digest
    return index


def load_runtime_index(config: CCOSRuntimeConfig) -> dict[str, object]:
    """Load and fully verify the canonical runtime index and all referenced records."""

    _validate_runtime_config_instance(config)
    index_path = config.artifact_root / "index.json"
    if not index_path.exists():
        return _empty_index()
    return _validate_index(config, _read_object(index_path, "CCOS runtime index"))


def _normalized_field_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _validate_payload(value: object, path: str) -> None:
    pending: list[tuple[object, str, int]] = [(value, path, 0)]
    observed_nodes = 0
    while pending:
        item, item_path, depth = pending.pop()
        observed_nodes += 1
        if observed_nodes > MAXIMUM_PAYLOAD_NODES:
            raise ValueError(f"CCOS payload exceeds the {MAXIMUM_PAYLOAD_NODES}-node structural limit.")
        if depth > MAXIMUM_PAYLOAD_DEPTH:
            raise ValueError(f"CCOS payload exceeds the {MAXIMUM_PAYLOAD_DEPTH}-level depth limit at {item_path}.")
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if not isinstance(key, str):
                    raise TypeError(f"CCOS payload object key must be a string at {item_path}.")
                normalized = _normalized_field_name(key)
                if normalized in PROHIBITED_FIELDS:
                    raise PermissionError(f"CCOS payload field is prohibited at {item_path}.{key}.")
                pending.append((nested, f"{item_path}.{key}", depth + 1))
            continue
        if isinstance(item, (list, tuple)):
            pending.extend(
                (nested, f"{item_path}[{index}]", depth + 1)
                for index, nested in enumerate(item)
            )
            continue
        if item is not None and not isinstance(item, (str, int, float, bool)):
            raise TypeError(f"CCOS payload value is not JSON-compatible at {item_path}.")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError(f"CCOS payload number must be finite at {item_path}.")


def _lock_path(config: CCOSRuntimeConfig) -> Path:
    return config.artifact_root / ".writer-lock"


def _recovery_lock_path(config: CCOSRuntimeConfig) -> Path:
    return config.artifact_root / ".recovery-lock"


def _process_exists(pid: int) -> bool:
    if pid < 1:
        raise ValueError("CCOS lock owner PID must be positive.")
    if os.name == "nt":
        query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(query_limited_information, False, pid)
        if not handle:
            if ctypes.get_last_error() == 5:
                return True
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_owner_pid(lock_path: Path) -> int:
    if lock_path.stat().st_size > 64:
        raise ValueError(f"CCOS lock owner metadata exceeds 64 bytes: {lock_path}")
    value = lock_path.read_text(encoding="ascii").strip()
    if not value.startswith("pid=") or not value[4:].isdigit():
        raise ValueError(f"CCOS lock owner metadata is invalid: {lock_path}")
    return int(value[4:])


def _create_lock_file(lock_path: Path) -> None:
    descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _quarantine_stale_writer_lock(config: CCOSRuntimeConfig, observed_at: float) -> bool:
    lock_path = _lock_path(config)
    try:
        age = observed_at - lock_path.stat().st_mtime
    except FileNotFoundError:
        return False
    if age <= config.stale_lock_seconds:
        return False
    owner_pid = _lock_owner_pid(lock_path)
    if _process_exists(owner_pid):
        raise TimeoutError(f"CCOS writer lock exceeds its stale threshold but owner PID {owner_pid} is still active.")
    quarantine = config.artifact_root / "recovery" / f"stale-lock-{uuid.uuid4().hex}"
    quarantine.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(lock_path, quarantine)
    except FileNotFoundError:
        return False
    return True


def _acquire_lock(config: CCOSRuntimeConfig) -> None:
    _PROCESS_WRITE_LOCK.acquire()
    try:
        config.artifact_root.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + config.lock_timeout_seconds
        while True:
            if _recovery_lock_path(config).exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"CCOS runtime recovery lock timed out: {_recovery_lock_path(config)}")
                time.sleep(min(0.01, config.lock_timeout_seconds / 10.0))
                continue
            try:
                _create_lock_file(_lock_path(config))
                return
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"CCOS runtime writer lock timed out: {_lock_path(config)}")
                time.sleep(min(0.01, config.lock_timeout_seconds / 10.0))
            except PermissionError as error:
                if time.monotonic() >= deadline:
                    if _lock_path(config).exists():
                        raise TimeoutError(f"CCOS runtime writer lock timed out: {_lock_path(config)}") from error
                    raise PermissionError(f"CCOS runtime writer lock cannot be created: {_lock_path(config)}") from error
                time.sleep(min(0.01, config.lock_timeout_seconds / 10.0))
    except Exception:
        _PROCESS_WRITE_LOCK.release()
        raise


def _release_lock(config: CCOSRuntimeConfig) -> None:
    try:
        _lock_path(config).unlink()
    except FileNotFoundError as error:
        raise RuntimeError("CCOS runtime writer lock disappeared before release.") from error
    finally:
        _PROCESS_WRITE_LOCK.release()


def _acquire_recovery_lock(config: CCOSRuntimeConfig) -> None:
    _PROCESS_WRITE_LOCK.acquire()
    try:
        config.artifact_root.mkdir(parents=True, exist_ok=True)
        try:
            _create_lock_file(_recovery_lock_path(config))
        except FileExistsError as error:
            raise TimeoutError(f"CCOS runtime recovery lock already exists: {_recovery_lock_path(config)}") from error
        deadline = time.monotonic() + config.lock_timeout_seconds
        while _lock_path(config).exists():
            _quarantine_stale_writer_lock(config, time.time())
            if not _lock_path(config).exists():
                break
            if time.monotonic() >= deadline:
                owner_pid = _lock_owner_pid(_lock_path(config))
                raise TimeoutError(f"CCOS recovery cannot proceed while writer PID {owner_pid} remains active.")
            time.sleep(min(0.01, config.lock_timeout_seconds / 10.0))
    except Exception as error:
        if _recovery_lock_path(config).exists():
            try:
                if _lock_owner_pid(_recovery_lock_path(config)) == os.getpid():
                    _recovery_lock_path(config).unlink()
            except (OSError, ValueError) as cleanup_error:
                error.add_note(f"CCOS recovery lock cleanup also failed: {cleanup_error}")
        _PROCESS_WRITE_LOCK.release()
        raise


def _release_recovery_lock(config: CCOSRuntimeConfig) -> None:
    try:
        if _lock_owner_pid(_recovery_lock_path(config)) != os.getpid():
            raise RuntimeError("CCOS runtime recovery lock ownership changed before release.")
        _recovery_lock_path(config).unlink()
    finally:
        _PROCESS_WRITE_LOCK.release()


def append_runtime_record(
    config: CCOSRuntimeConfig,
    kind: str,
    payload: Mapping[str, object],
    recorded_at: datetime,
) -> dict[str, object]:
    """Append one immutable evidence record without granting authority."""

    if kind not in RECORD_KINDS:
        raise ValueError(f"Unsupported CCOS runtime record kind: {kind}")
    if recorded_at.tzinfo is None:
        raise ValueError("CCOS runtime record time must be timezone-aware.")
    normalized_payload = dict(payload)
    _validate_payload(normalized_payload, "payload")
    encoded_payload = _canonical_bytes(normalized_payload)
    if len(encoded_payload) > config.maximum_record_bytes:
        raise ValueError(
            f"CCOS runtime payload exceeds maximumRecordBytes: {len(encoded_payload)} > {config.maximum_record_bytes}."
        )
    _acquire_lock(config)
    try:
        index = load_runtime_index(config)
        raw_records = index["records"]
        if not isinstance(raw_records, list):
            raise TypeError("Verified CCOS index unexpectedly lacks a record list.")
        if len(raw_records) >= config.maximum_records:
            raise RuntimeError(
                f"CCOS runtime record capacity is exhausted: {len(raw_records)} >= {config.maximum_records}."
            )
        previous_digest = str(index["headDigest"]) if index["headDigest"] is not None else None
        core: dict[str, object] = {
            "schema": RECORD_SCHEMA,
            "sequence": len(raw_records),
            "previousDigest": previous_digest,
            "kind": kind,
            "recordedAt": recorded_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "payloadDigest": canonical_digest(normalized_payload),
            "payload": normalized_payload,
            "authorityGranted": False,
            "providerCalls": 0,
            "automaticActivation": False,
        }
        digest = canonical_digest(core)
        record = core | {"recordDigest": digest}
        record_path = config.artifact_root / "records" / f"{digest}.json"
        if record_path.exists():
            existing = _validate_record(_read_object(record_path, "CCOS record"), digest)
            if existing != record:
                raise FileExistsError(f"CCOS immutable record collision: {record_path}")
        else:
            _atomic_write(record_path, record)
        index_path = config.artifact_root / "index.json"
        if index_path.is_file():
            _atomic_write(config.artifact_root / "recovery" / "last-known-good-index.json", index)
        updated_records = [*raw_records, digest]
        updated_core: dict[str, object] = {
            "schema": INDEX_SCHEMA,
            "generation": len(updated_records),
            "headDigest": digest,
            "records": updated_records,
        }
        updated_index = updated_core | {"indexDigest": canonical_digest(updated_core)}
        _atomic_write(index_path, updated_index)
        return record
    finally:
        _release_lock(config)


def _records_for_index(
    config: CCOSRuntimeConfig,
    index: Mapping[str, object],
    requested: Sequence[str],
) -> tuple[dict[str, object], ...]:
    raw_records = index["records"]
    if not isinstance(raw_records, list):
        raise TypeError("Verified CCOS index unexpectedly lacks a record list.")
    records: list[dict[str, object]] = []
    for raw_digest in raw_records:
        digest = require_digest(str(raw_digest), "CCOS index record")
        record = _validate_record(
            _read_object(config.artifact_root / "records" / f"{digest}.json", "CCOS record"), digest
        )
        if not requested or record["kind"] in requested:
            records.append(record)
    return tuple(records)


def runtime_records(config: CCOSRuntimeConfig, kinds: Sequence[str]) -> tuple[dict[str, object], ...]:
    requested = tuple(kinds)
    if any(kind not in RECORD_KINDS for kind in requested):
        raise ValueError("CCOS record query contains an unsupported kind.")
    index = load_runtime_index(config)
    return _records_for_index(config, index, requested)


def require_runtime_capacity(config: CCOSRuntimeConfig, additional_records: int) -> dict[str, int]:
    if additional_records < 1:
        raise ValueError("CCOS additional record capacity must be positive.")
    index = load_runtime_index(config)
    generation = int(index["generation"])
    remaining = config.maximum_records - generation
    if additional_records > remaining:
        raise RuntimeError(
            f"CCOS runtime lacks capacity for {additional_records} records: {remaining} of {config.maximum_records} remain."
        )
    return {
        "generation": generation,
        "maximumRecords": config.maximum_records,
        "remainingRecords": remaining,
        "requestedRecords": additional_records,
    }


def runtime_state(config: CCOSRuntimeConfig) -> dict[str, object]:
    index = load_runtime_index(config)
    records = _records_for_index(config, index, ())
    counts = {kind: sum(record["kind"] == kind for record in records) for kind in RECORD_KINDS}
    return {
        "schema": STATE_SCHEMA,
        "status": "OPERATIONAL_OBSERVATION_ONLY",
        "artifactRoot": config.artifact_root.as_posix(),
        "generation": index["generation"],
        "headDigest": index["headDigest"],
        "indexDigest": index["indexDigest"],
        "counts": counts,
        "retentionDays": dict(config.retention_days),
        "maximumRecords": config.maximum_records,
        "authority": {
            "providerCallsAllowed": config.provider_calls_allowed,
            "automaticActivationAllowed": config.automatic_activation_allowed,
            "federationAllowed": config.federation_allowed,
            "metaEvolutionAllowed": config.meta_evolution_allowed,
        },
        "authorityGranted": False,
        "providerCalls": 0,
    }


def retention_report(config: CCOSRuntimeConfig, observed_at: datetime) -> dict[str, object]:
    """Report projection-retention candidates while preserving canonical evidence."""

    if observed_at.tzinfo is None:
        raise ValueError("CCOS retention observation time must be timezone-aware.")
    index = load_runtime_index(config)
    records = _records_for_index(config, index, ())
    candidates: list[dict[str, object]] = []
    for record in records:
        kind = str(record["kind"])
        recorded_at = datetime.fromisoformat(str(record["recordedAt"]).replace("Z", "+00:00"))
        age_days = max(0.0, (observed_at - recorded_at).total_seconds() / 86_400.0)
        retention_days = config.retention_days[kind]
        if age_days >= retention_days:
            candidates.append(
                {
                    "recordDigest": record["recordDigest"],
                    "kind": kind,
                    "ageDays": round(age_days, 6),
                    "retentionDays": retention_days,
                    "recommendedAction": "TOMBSTONE_PROJECTION_ONLY",
                    "canonicalEvidencePreserved": True,
                }
            )
    return {
        "schema": "obtuse.blunder.ccos-retention-report.v1",
        "status": "REVIEW_REQUIRED" if candidates else "WITHIN_POLICY",
        "sourceIndexDigest": index["indexDigest"],
        "candidateCount": len(candidates),
        "candidates": candidates,
        "canonicalRecordsDeleted": 0,
        "authorityGranted": False,
        "automaticDeletion": False,
    }


def inspect_runtime_recovery(config: CCOSRuntimeConfig, observed_at: datetime) -> dict[str, object]:
    """Inspect interrupted-write evidence without mutating or accepting it."""

    if observed_at.tzinfo is None:
        raise ValueError("CCOS recovery inspection time must be timezone-aware.")
    index_status = "VERIFIED"
    index_error: str | None = None
    indexed: set[str] = set()
    try:
        index = load_runtime_index(config)
        raw_records = index["records"]
        if not isinstance(raw_records, list):
            raise TypeError("Verified CCOS index unexpectedly lacks a record list.")
        indexed = {require_digest(str(item), "CCOS index record") for item in raw_records}
    except (FileNotFoundError, ValueError, TypeError, PermissionError, KeyError) as error:
        index_status = "INVALID_FAIL_CLOSED"
        index_error = str(error)
    valid_orphans: list[str] = []
    invalid_records: list[dict[str, str]] = []
    records_root = config.artifact_root / "records"
    for path in sorted(records_root.glob("*.json")) if records_root.is_dir() else ():
        if path.stem in indexed:
            continue
        try:
            digest = require_digest(path.stem, "CCOS orphan record filename")
            _validate_record(_read_object(path, "CCOS orphan record"), digest)
            valid_orphans.append(digest)
        except (ValueError, TypeError, PermissionError, KeyError) as error:
            invalid_records.append({"path": path.as_posix(), "error": str(error)})
    temporary_files = sorted(path.as_posix() for path in config.artifact_root.rglob("*.tmp")) if config.artifact_root.is_dir() else []
    lock_path = _lock_path(config)
    lock_age_seconds: float | None = None
    if lock_path.exists():
        lock_age_seconds = max(0.0, observed_at.timestamp() - lock_path.stat().st_mtime)
    recovery_required = (
        index_status != "VERIFIED"
        or bool(valid_orphans)
        or bool(invalid_records)
        or bool(temporary_files)
        or lock_path.exists()
    )
    return {
        "schema": "obtuse.blunder.ccos-runtime-recovery-inspection.v1",
        "status": "RECOVERY_REVIEW_REQUIRED" if recovery_required else "CLEAN",
        "indexStatus": index_status,
        "indexError": index_error,
        "validOrphanRecordDigests": valid_orphans,
        "invalidRecords": invalid_records,
        "temporaryFiles": temporary_files,
        "writerLockPresent": lock_path.exists(),
        "writerLockAgeSeconds": round(lock_age_seconds, 6) if lock_age_seconds is not None else None,
        "staleLockThresholdSeconds": config.stale_lock_seconds,
        "automaticRepair": False,
        "authorityGranted": False,
    }


def recover_last_known_good(config: CCOSRuntimeConfig, confirmation: str) -> dict[str, object]:
    """Restore the last verified index only after exact operator confirmation."""

    if confirmation != RECOVERY_CONFIRMATION:
        raise PermissionError(f"CCOS recovery requires exact confirmation: {RECOVERY_CONFIRMATION}")
    backup_path = config.artifact_root / "recovery" / "last-known-good-index.json"
    if not backup_path.is_file():
        raise FileNotFoundError(f"CCOS last-known-good index is missing: {backup_path}")
    _acquire_recovery_lock(config)
    try:
        backup = _validate_index(config, _read_object(backup_path, "CCOS last-known-good index"))
        current_path = config.artifact_root / "index.json"
        if current_path.is_file():
            corrupt_copy = config.artifact_root / "recovery" / f"replaced-index-{uuid.uuid4().hex}.json"
            shutil.copyfile(current_path, corrupt_copy)
        _atomic_write(current_path, backup)
        verified = load_runtime_index(config)
        return {
            "schema": "obtuse.blunder.ccos-runtime-recovery.v1",
            "status": "RECOVERED_LAST_KNOWN_GOOD",
            "generation": verified["generation"],
            "headDigest": verified["headDigest"],
            "indexDigest": verified["indexDigest"],
            "authorityGranted": False,
            "automaticActivation": False,
        }
    finally:
        _release_recovery_lock(config)


def provider_free_canary(config: CCOSRuntimeConfig, observed_at: datetime) -> dict[str, object]:
    """Exercise durable write and reload while preserving an honest no-champion result."""

    seed = {
        "canaryId": canonical_digest({"runtime": config.artifact_root.as_posix(), "observedAt": observed_at.isoformat()}),
        "checks": ["content-addressed-write", "hash-chain-reload", "authority-closed"],
        "heldOutEvaluation": "NO_CHAMPION_INSUFFICIENT_REALITY_EVIDENCE",
    }
    record = append_runtime_record(config, "canary", seed, observed_at)
    reloaded = runtime_records(config, ("canary",))
    exact = any(item["recordDigest"] == record["recordDigest"] for item in reloaded)
    return {
        "schema": "obtuse.blunder.ccos-provider-free-canary.v1",
        "status": "PASS" if exact else "FAIL_RELOAD_MISMATCH",
        "recordDigest": record["recordDigest"],
        "exactReload": exact,
        "heldOutEvaluation": "NO_CHAMPION_INSUFFICIENT_REALITY_EVIDENCE",
        "providerCalls": 0,
        "authorityGranted": False,
        "automaticActivation": False,
    }
