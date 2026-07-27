"""Canonical serialization and strict validation primitives for DumbMoney.

Cross-process identities, signatures, and ledger hashes all use the exact JSON
encoding defined here.  Financial amounts are integers in their declared
minor unit; floats are intentionally rejected from signed contracts.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping as ABCMapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, cast


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented in the signed JSON domain."""


def _validate_json_domain(value: object, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError(f"{path} contains a non-finite number")
        raise CanonicalizationError(f"{path} contains a float; signed contracts require integer units")
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_json_domain(item, f"{path}[{index}]")
        return
    if isinstance(value, ABCMapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"{path} contains a non-string object key")
            _validate_json_domain(item, f"{path}.{key}")
        return
    raise CanonicalizationError(f"{path} contains unsupported type {type(value).__name__}")


def _plain_json(value: object) -> object:
    if isinstance(value, ABCMapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    if isinstance(value, list):
        return [_plain_json(item) for item in value]
    return value


def canonical_json_bytes(value: object) -> bytes:
    """Return the sole canonical JSON encoding used by DumbMoney."""

    _validate_json_domain(value)
    return json.dumps(
        _plain_json(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    """Return a lowercase SHA-256 digest of a canonical JSON value."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def bytes_sha256(value: bytes) -> str:
    """Return a lowercase SHA-256 digest of opaque bytes."""

    return hashlib.sha256(value).hexdigest()


def format_utc(value: datetime, context: str = "timestamp") -> str:
    """Format an aware timestamp as canonical RFC3339 UTC with ``Z``."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{context} must include an explicit timezone")
    utc = value.astimezone(timezone.utc)
    if utc.microsecond:
        rendered = utc.isoformat(timespec="microseconds")
    else:
        rendered = utc.isoformat(timespec="seconds")
    return rendered.replace("+00:00", "Z")


def parse_utc(value: str, context: str = "timestamp") -> datetime:
    """Parse an explicitly zoned timestamp and normalize it to UTC."""

    if not isinstance(value, str) or not value:
        raise TypeError(f"{context} must be a non-empty RFC3339 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{context} must be a valid RFC3339 timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{context} must include an explicit timezone")
    normalized = parsed.astimezone(timezone.utc)
    if format_utc(normalized, context) != value:
        raise ValueError(f"{context} must use canonical UTC RFC3339 formatting with Z")
    return normalized


def require_identifier(value: str, context: str) -> str:
    if not isinstance(value, str) or IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{context} must be a valid non-empty identifier")
    return value


def require_digest(value: str, context: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{context} must be 64 lowercase hexadecimal characters")
    return value


def require_nonnegative_int(value: int, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{context} must be a non-negative integer")
    return value


def require_positive_int(value: int, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{context} must be a positive integer")
    return value


def require_sorted_unique(values: tuple[str, ...], context: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not all(isinstance(item, str) and item for item in values):
        raise TypeError(f"{context} must be a tuple of non-empty strings")
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{context} must be sorted and contain no duplicates")
    return values


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


def loads_strict_json(raw: str | bytes, context: str = "JSON") -> Mapping[str, object]:
    """Load a JSON object while rejecting duplicate keys and invalid roots."""

    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{context} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise TypeError(f"{context} root must be an object")
    _validate_json_domain(value)
    return cast(Mapping[str, object], value)


def load_strict_json(path: Path) -> Mapping[str, object]:
    if not path.is_file():
        raise FileNotFoundError(f"required JSON file is missing: {path}")
    return loads_strict_json(path.read_text(encoding="utf-8"), str(path))
