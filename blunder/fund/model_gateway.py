"""Fail-closed OpenRouter gateway for the DumbMoney research mesh.

This module is deliberately independent from the rest of the control plane:
it has no broker authority, never persists prompts or completions, and accepts
only a small typed request surface.  The durable state machine makes the
network uncertainty boundary explicit:

``RESERVED -> DISPATCHING -> SETTLED``

Any uncertainty after a reservation becomes ``AMBIGUOUS``.  Ambiguous spend is
never retried or released automatically and pauses the UTC budget day.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Protocol, cast

from blunder.fund.canonical import canonical_json_bytes, canonical_sha256, format_utc


OPENROUTER_ORIGIN = "https://openrouter.ai"
KEY_STATUS_URL = f"{OPENROUTER_ORIGIN}/api/v1/key"
CHAT_COMPLETIONS_URL = f"{OPENROUTER_ORIGIN}/api/v1/chat/completions"
GENERATION_URL = f"{OPENROUTER_ORIGIN}/api/v1/generation"

MICRO_USD_PER_USD = 1_000_000
DAILY_CAP_MICROUSD = 10 * MICRO_USD_PER_USD


class BudgetCategory(str, Enum):
    """The fixed daily allocation lanes."""

    RESEARCH = "research"
    EVALUATION = "evaluation"
    OPERATIONS = "operations"


CATEGORY_LIMITS_MICROUSD: Mapping[BudgetCategory, int] = MappingProxyType(
    {
        BudgetCategory.RESEARCH: 6_000_000,
        BudgetCategory.EVALUATION: 2_000_000,
        BudgetCategory.OPERATIONS: 2_000_000,
    }
)


class RequestState(str, Enum):
    RESERVED = "RESERVED"
    DISPATCHING = "DISPATCHING"
    SETTLED = "SETTLED"
    AMBIGUOUS = "AMBIGUOUS"


class ModelGatewayError(RuntimeError):
    """Base class for gateway failures."""


class ConfigurationError(ModelGatewayError):
    """The immutable gateway policy is invalid."""


class SensitivePayloadRejected(ModelGatewayError):
    """A prompt appears to contain broker, account, or secret material."""


class BudgetExceeded(ModelGatewayError):
    """A reservation would exceed its category or the global daily limit."""


class GatewayPaused(ModelGatewayError):
    """The gateway is paused because durable state is uncertain or drifted."""


class DuplicateRequest(ModelGatewayError):
    """A request id was reused with different content or is still unresolved."""


class AttestationError(ModelGatewayError):
    """The dedicated OpenRouter key does not satisfy the required policy."""


class ReconciliationError(ModelGatewayError):
    """Provider usage records cannot be reconciled exactly."""


class TransportError(ModelGatewayError):
    """An HTTP operation failed or returned an unusable response."""


@dataclass(frozen=True)
class ModelRoute:
    """One concrete, allowlisted OpenRouter model/provider route."""

    model: str
    provider: str
    provider_name: str
    max_output_tokens: int
    max_price: Mapping[str, int]

    def __post_init__(self) -> None:
        _require_concrete_slug(self.model, "model", require_slash=True)
        _require_concrete_slug(self.provider, "provider", require_slash=False)
        if (
            not isinstance(self.provider_name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._()/+-]{0,127}", self.provider_name)
        ):
            raise ConfigurationError("provider_name must be the concrete router metadata name")
        if not isinstance(self.max_output_tokens, int) or isinstance(self.max_output_tokens, bool):
            raise ConfigurationError("max_output_tokens must be an integer")
        if not 1 <= self.max_output_tokens <= 32_768:
            raise ConfigurationError("max_output_tokens must be between 1 and 32768")
        allowed_price_fields = {"prompt", "completion", "request", "image"}
        if not self.max_price:
            raise ConfigurationError("max_price must contain at least one price ceiling")
        normalized: dict[str, int] = {}
        for key, value in self.max_price.items():
            if key not in allowed_price_fields:
                raise ConfigurationError(f"unsupported max_price field: {key}")
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ConfigurationError(f"max_price.{key} must be a non-negative integer")
            normalized[key] = value
        object.__setattr__(self, "max_price", MappingProxyType(normalized))


@dataclass(frozen=True)
class GatewayConfig:
    """Immutable production policy for one dedicated key."""

    database_path: Path
    expected_key_label: str
    routes: Mapping[BudgetCategory, ModelRoute]
    request_timeout_seconds: int = 45
    attestation_max_age_seconds: int = 30
    near_midnight_fence_seconds: int = 300
    max_prompt_characters: int = 200_000
    max_response_bytes: int = 2_000_000

    def __post_init__(self) -> None:
        if not isinstance(self.database_path, Path):
            object.__setattr__(self, "database_path", Path(self.database_path))
        if not self.expected_key_label or any(ch in self.expected_key_label for ch in "\r\n"):
            raise ConfigurationError("expected_key_label must be a non-empty single line")
        route_keys = set(self.routes)
        if route_keys != set(BudgetCategory):
            missing = sorted(category.value for category in set(BudgetCategory) - route_keys)
            extra = sorted(str(category) for category in route_keys - set(BudgetCategory))
            raise ConfigurationError(f"routes must cover the fixed categories; missing={missing}, extra={extra}")
        object.__setattr__(self, "routes", MappingProxyType(dict(self.routes)))
        for name, value, minimum, maximum in (
            ("request_timeout_seconds", self.request_timeout_seconds, 1, 120),
            ("attestation_max_age_seconds", self.attestation_max_age_seconds, 1, 120),
            ("near_midnight_fence_seconds", self.near_midnight_fence_seconds, 60, 3_600),
            ("max_prompt_characters", self.max_prompt_characters, 1, 1_000_000),
            ("max_response_bytes", self.max_response_bytes, 1_024, 10_000_000),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
                raise ConfigurationError(f"{name} must be an integer in [{minimum}, {maximum}]")
        minimum_fence = (2 * self.request_timeout_seconds) + self.attestation_max_age_seconds
        if self.near_midnight_fence_seconds < minimum_fence:
            raise ConfigurationError(
                "near_midnight_fence_seconds must cover two HTTP operations and attestation age"
            )


@dataclass(frozen=True)
class GatewayRequest:
    """A bounded text-only completion request."""

    request_id: str
    category: BudgetCategory
    messages: Sequence[Mapping[str, str]]
    reserve_microusd: int
    max_output_tokens: int


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: Mapping[str, Any]
    received_bytes: int


class JsonTransport(Protocol):
    def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any] | None,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> HttpResponse:
        """Perform exactly one HTTP request and return decoded JSON."""


SecretLoader = Callable[[], str]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class CompletionResult:
    request_id: str
    state: RequestState
    generation_id: str | None
    actual_cost_microusd: int | None
    response_digest: str | None
    response: Mapping[str, Any] | None
    replayed: bool


@dataclass(frozen=True)
class GatewayStatus:
    utc_day: str
    paused: bool
    pause_reason: str | None
    settled_microusd: int
    reserved_microusd: int
    effective_server_usage_microusd: int | None
    remaining_microusd: int
    category_spend_microusd: Mapping[str, int]
    unresolved_requests: tuple[str, ...]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _load_openrouter_json(raw: bytes) -> Mapping[str, Any]:
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise TransportError("OpenRouter returned invalid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise TransportError("OpenRouter returned a non-object JSON response")
    return cast(Mapping[str, Any], decoded)


class StdlibHttpsTransport:
    """Small production transport with no retry and no cross-origin redirect."""

    def __init__(self) -> None:
        context = ssl.create_default_context()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context),
            _NoRedirect(),
        )

    def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any] | None,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> HttpResponse:
        method = method.upper()
        if method not in {"GET", "POST"}:
            raise TransportError("only GET and POST are supported")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "openrouter.ai" or parsed.port not in (None, 443):
            raise TransportError("refusing non-OpenRouter HTTPS destination")
        encoded: bytes | None = None
        if body is not None:
            encoded = json.dumps(
                body,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=encoded,
            headers=dict(headers),
            method=method,
        )
        try:
            response = self._opener.open(request, timeout=timeout_seconds)
            with response:
                raw = response.read(max_response_bytes + 1)
                status = int(response.status)
                final_url = response.geturl()
        except urllib.error.HTTPError as exc:
            raw = exc.read(max_response_bytes + 1)
            status = int(exc.code)
            final_url = exc.geturl()
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise TransportError("OpenRouter HTTP operation failed") from exc
        final = urllib.parse.urlsplit(final_url)
        if final.scheme != "https" or final.hostname != "openrouter.ai" or final.port not in (None, 443):
            raise TransportError("OpenRouter response crossed an origin boundary")
        if len(raw) > max_response_bytes:
            raise TransportError("OpenRouter response exceeded the byte ceiling")
        decoded = _load_openrouter_json(raw)
        return HttpResponse(
            status=status,
            body=decoded,
            received_bytes=len(raw),
        )


class WindowsCredentialSecretLoader:
    """Load one generic credential under the dedicated service identity.

    Only the non-secret Credential Manager target name is configured. The
    credential value never crosses a command line, environment variable, JSON
    configuration file, or journal.
    """

    def __init__(self, target_name: str = "DumbMoney/OpenRouter") -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ./_:-]{0,255}", target_name):
            raise ConfigurationError("Windows credential target name is invalid")
        self.target_name = target_name

    def __call__(self) -> str:
        import ctypes
        import os
        from ctypes import wintypes

        if os.name != "nt":
            raise ConfigurationError(
                "Windows Credential Manager secret loading requires Windows"
            )

        class FileTime(ctypes.Structure):
            _fields_ = [
                ("low_datetime", wintypes.DWORD),
                ("high_datetime", wintypes.DWORD),
            ]

        class Credential(ctypes.Structure):
            _fields_ = [
                ("flags", wintypes.DWORD),
                ("credential_type", wintypes.DWORD),
                ("target_name", wintypes.LPWSTR),
                ("comment", wintypes.LPWSTR),
                ("last_written", FileTime),
                ("credential_blob_size", wintypes.DWORD),
                ("credential_blob", ctypes.POINTER(ctypes.c_ubyte)),
                ("persist", wintypes.DWORD),
                ("attribute_count", wintypes.DWORD),
                ("attributes", ctypes.c_void_p),
                ("target_alias", wintypes.LPWSTR),
                ("user_name", wintypes.LPWSTR),
            ]

        credential_pointer = ctypes.POINTER(Credential)()
        credential_read = ctypes.windll.advapi32.CredReadW
        credential_read.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(Credential)),
        ]
        credential_read.restype = wintypes.BOOL
        credential_free = ctypes.windll.advapi32.CredFree
        credential_free.argtypes = [ctypes.c_void_p]
        credential_free.restype = None
        if not credential_read(self.target_name, 1, 0, ctypes.byref(credential_pointer)):
            raise ConfigurationError(
                "required Windows credential is unavailable to this service identity"
            )
        try:
            credential = credential_pointer.contents
            size = int(credential.credential_blob_size)
            if size <= 0 or size > 16_384:
                raise ConfigurationError("Windows credential has an invalid byte length")
            raw = ctypes.string_at(credential.credential_blob, size)
        finally:
            credential_free(credential_pointer)
        try:
            value = (
                raw.decode("utf-16-le") if b"\x00" in raw else raw.decode("utf-8")
            ).rstrip("\x00")
        except UnicodeDecodeError as exc:
            raise ConfigurationError(
                "Windows credential is not valid UTF-8 or UTF-16 text"
            ) from exc
        if not value or any(character in value for character in "\r\n\x00"):
            raise ConfigurationError("Windows credential value is empty or malformed")
        return value


class OpenRouterModelGateway:
    """Durable single-flight OpenRouter budget and privacy boundary."""

    _SCHEMA_VERSION = 1

    def __init__(
        self,
        config: GatewayConfig,
        *,
        transport: JsonTransport,
        secret_loader: SecretLoader,
        clock: Clock | None = None,
        recover_uncertain: bool = True,
    ) -> None:
        self.config = config
        self._transport = transport
        self._secret_loader = secret_loader
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._mutex = threading.RLock()
        self.config.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            self.config.database_path,
            isolation_level=None,
            check_same_thread=False,
        )
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._initialize_schema()
        if recover_uncertain:
            self._recover_uncertain_state()

    def close(self) -> None:
        with self._mutex:
            self._db.close()

    def __enter__(self) -> "OpenRouterModelGateway":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def complete(self, request: GatewayRequest) -> CompletionResult:
        """Dispatch one completion with no POST retry.

        A duplicate settled request returns settlement metadata with
        ``response=None`` because completions are intentionally not persisted.
        """

        with self._mutex:
            now = self._now()
            route, normalized_messages, request_digest = self._validate_request(request, now)
            existing = self._existing_request(request.request_id)
            if existing is not None:
                return self._duplicate_result(existing, request_digest)
            self._assert_not_near_midnight(now)
            day = now.date().isoformat()
            self._reserve(request, route, request_digest, day, now)
            try:
                secret = self._load_secret()
                attested_usage = self._attest_key(secret, day)
                self._validate_reserved_against_server(request.request_id, day, attested_usage)
                self._assert_dispatch_window(day)
                request_body = self._wire_request(route, normalized_messages, request.max_output_tokens)
                self._mark_dispatching(request.request_id)
                response = self._transport.request_json(
                    method="POST",
                    url=CHAT_COMPLETIONS_URL,
                    headers=self._headers(secret),
                    body=request_body,
                    timeout_seconds=self.config.request_timeout_seconds,
                    max_response_bytes=self.config.max_response_bytes,
                )
                if response.status != 200:
                    raise TransportError(f"OpenRouter completion returned HTTP {response.status}")
                generation_id, usage_cost = self._validate_completion_response(
                    response,
                    route,
                    request.max_output_tokens,
                )
                self._assert_same_day(day)
                generation = self._transport.request_json(
                    method="GET",
                    url=f"{GENERATION_URL}?{urllib.parse.urlencode({'id': generation_id})}",
                    headers=self._headers(secret),
                    body=None,
                    timeout_seconds=self.config.request_timeout_seconds,
                    max_response_bytes=self.config.max_response_bytes,
                )
                if generation.status != 200:
                    raise TransportError(f"OpenRouter generation lookup returned HTTP {generation.status}")
                generation_cost = self._generation_cost(generation, generation_id, route)
                if usage_cost != generation_cost:
                    raise ReconciliationError("usage.cost does not equal generation total_cost")
                actual_microusd = _decimal_to_microusd(usage_cost, "usage.cost")
                response_digest = canonical_sha256(_json_digest_domain(response.body))
                self._assert_same_day(day)
                return self._settle(
                    request.request_id,
                    generation_id,
                    actual_microusd,
                    response_digest,
                    response.body,
                )
            except Exception as exc:
                self._lock_and_pause(request.request_id, day, _safe_reason(exc))
                if isinstance(exc, ModelGatewayError):
                    raise
                raise ModelGatewayError("model gateway failed closed") from exc

    def status(self, *, at: datetime | None = None) -> GatewayStatus:
        with self._mutex:
            now = self._normalize_time(at or self._now())
            day = now.date().isoformat()
            row = self._db.execute(
                "SELECT * FROM model_daily_state WHERE utc_day = ?",
                (day,),
            ).fetchone()
            totals = self._db.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN state = 'SETTLED' THEN actual_microusd ELSE 0 END), 0)
                        AS settled,
                    COALESCE(SUM(CASE WHEN state IN ('RESERVED','DISPATCHING','AMBIGUOUS')
                                      THEN reserve_microusd ELSE 0 END), 0) AS reserved
                FROM model_requests WHERE utc_day = ?
                """,
                (day,),
            ).fetchone()
            category_rows = self._db.execute(
                """
                SELECT category,
                       COALESCE(SUM(CASE WHEN state = 'SETTLED' THEN actual_microusd
                                         ELSE reserve_microusd END), 0) AS spend
                FROM model_requests WHERE utc_day = ? GROUP BY category
                """,
                (day,),
            ).fetchall()
            categories = {category.value: 0 for category in BudgetCategory}
            categories.update({str(item["category"]): int(item["spend"]) for item in category_rows})
            unresolved = tuple(
                str(item["request_id"])
                for item in self._db.execute(
                    """
                    SELECT request_id FROM model_requests
                    WHERE state IN ('RESERVED','DISPATCHING','AMBIGUOUS')
                    ORDER BY created_at, request_id
                    """
                ).fetchall()
            )
            settled = int(totals["settled"])
            reserved = int(totals["reserved"])
            server_usage = None if row is None else row["server_usage_microusd"]
            local_at_attestation = (
                0
                if row is None or row["local_settled_at_attestation_microusd"] is None
                else int(row["local_settled_at_attestation_microusd"])
            )
            if server_usage is None:
                effective = settled + reserved
            else:
                effective = (
                    int(server_usage)
                    + max(0, settled - local_at_attestation)
                    + reserved
                )
            return GatewayStatus(
                utc_day=day,
                paused=bool(row["paused"]) if row is not None else bool(unresolved),
                pause_reason=cast(str | None, row["pause_reason"]) if row is not None else None,
                settled_microusd=settled,
                reserved_microusd=reserved,
                effective_server_usage_microusd=(
                    int(server_usage) if server_usage is not None else None
                ),
                remaining_microusd=max(0, DAILY_CAP_MICROUSD - effective),
                category_spend_microusd=MappingProxyType(categories),
                unresolved_requests=unresolved,
            )

    def journal_entries(self) -> tuple[Mapping[str, Any], ...]:
        """Return the canonical digest-only state journal."""

        with self._mutex:
            rows = self._db.execute(
                "SELECT canonical_event FROM model_journal ORDER BY sequence"
            ).fetchall()
            return tuple(
                cast(Mapping[str, Any], json.loads(str(row["canonical_event"])))
                for row in rows
            )

    def redacted_control_snapshot(self, *, at: datetime | None = None) -> Mapping[str, Any]:
        """Return budget-only telemetry suitable for the Core control snapshot.

        This adapter intentionally omits request ids, generation ids, route
        identifiers, key labels, prompts, completions, and raw error text.
        """

        observed = self._normalize_time(at or self._now())
        status = self.status(at=observed)
        reset = datetime.combine(
            observed.date() + timedelta(days=1),
            time.min,
            tzinfo=timezone.utc,
        )
        committed = DAILY_CAP_MICROUSD - status.remaining_microusd
        spent = max(0, committed - status.reserved_microusd)
        reason = status.pause_reason
        safe_reason = (
            reason
            if reason is not None and re.fullmatch(r"[A-Z0-9_]{1,80}", reason)
            else ("REDACTED" if reason else None)
        )
        categories = {
            category.value: {
                "limit_microusd": CATEGORY_LIMITS_MICROUSD[category],
                "committed_microusd": int(
                    status.category_spend_microusd.get(category.value, 0)
                ),
            }
            for category in BudgetCategory
        }
        return MappingProxyType(
            {
                "schema": "dumbmoney.openrouter-budget-status.v1",
                "provider": "OpenRouter",
                "utc_day": status.utc_day,
                "daily_budget_microusd": DAILY_CAP_MICROUSD,
                "daily_budget_cents": DAILY_CAP_MICROUSD // 10_000,
                "spent_microusd": spent,
                "spent_cents": _micro_to_conservative_cents(spent),
                "reserved_microusd": status.reserved_microusd,
                "committed_microusd": committed,
                "remaining_microusd": status.remaining_microusd,
                "remaining_cents": status.remaining_microusd // 10_000,
                "resets_at": format_utc(reset),
                "research_paused": status.paused,
                "pause_reason_code": safe_reason,
                "unresolved_count": len(status.unresolved_requests),
                "categories": categories,
            }
        )

    def _initialize_schema(self) -> None:
        self._db.executescript(
            """
                CREATE TABLE IF NOT EXISTS model_gateway_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                ) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS model_requests (
                    request_id TEXT PRIMARY KEY,
                    utc_day TEXT NOT NULL,
                    category TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    model_digest TEXT NOT NULL,
                    provider_digest TEXT NOT NULL,
                    reserve_microusd INTEGER NOT NULL CHECK (reserve_microusd > 0),
                    actual_microusd INTEGER,
                    state TEXT NOT NULL CHECK (
                        state IN ('RESERVED','DISPATCHING','SETTLED','AMBIGUOUS')
                    ),
                    generation_id TEXT UNIQUE,
                    generation_digest TEXT,
                    response_digest TEXT,
                    failure_reason_digest TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_model_requests_day_state
                    ON model_requests(utc_day, state);
                CREATE TABLE IF NOT EXISTS model_daily_state (
                    utc_day TEXT PRIMARY KEY,
                    server_usage_microusd INTEGER,
                    local_settled_at_attestation_microusd INTEGER,
                    attested_at TEXT,
                    attestation_digest TEXT,
                    paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0,1)),
                    pause_reason TEXT
                ) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS model_journal (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    previous_event_id TEXT,
                    canonical_event TEXT NOT NULL
                );
            """
        )
        with self._transaction():
            current = self._db.execute(
                "SELECT value FROM model_gateway_meta WHERE key = 'schema_version'"
            ).fetchone()
            if current is None:
                self._db.execute(
                    "INSERT INTO model_gateway_meta(key, value) VALUES ('schema_version', ?)",
                    (str(self._SCHEMA_VERSION),),
                )
            elif int(current["value"]) != self._SCHEMA_VERSION:
                raise ConfigurationError("unsupported model gateway database schema")

    def _recover_uncertain_state(self) -> None:
        now = self._now()
        with self._transaction():
            rows = self._db.execute(
                """
                SELECT request_id, utc_day, request_digest, state
                FROM model_requests WHERE state IN ('RESERVED','DISPATCHING')
                """
            ).fetchall()
            for row in rows:
                reason = f"RESTART_WITH_{row['state']}"
                self._db.execute(
                    """
                    UPDATE model_requests
                    SET state = 'AMBIGUOUS', failure_reason_digest = ?, updated_at = ?
                    WHERE request_id = ?
                    """,
                    (canonical_sha256(reason), format_utc(now), row["request_id"]),
                )
                self._pause_day(str(row["utc_day"]), reason)
                self._append_journal(
                    event_type="RECOVERY_AMBIGUOUS",
                    request_id=str(row["request_id"]),
                    request_digest=str(row["request_digest"]),
                    state=RequestState.AMBIGUOUS,
                    at=now,
                    reason=reason,
                )

    def _validate_request(
        self,
        request: GatewayRequest,
        now: datetime,
    ) -> tuple[ModelRoute, tuple[dict[str, str], ...], str]:
        if not isinstance(request.request_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request.request_id
        ):
            raise ModelGatewayError("request_id must be a stable non-secret identifier")
        try:
            category = (
                request.category
                if isinstance(request.category, BudgetCategory)
                else BudgetCategory(request.category)
            )
        except (TypeError, ValueError) as exc:
            raise ModelGatewayError("category is not an allowed budget category") from exc
        route = self.config.routes[category]
        if (
            not isinstance(request.reserve_microusd, int)
            or isinstance(request.reserve_microusd, bool)
            or request.reserve_microusd <= 0
        ):
            raise ModelGatewayError("reserve_microusd must be a positive integer")
        if request.reserve_microusd > CATEGORY_LIMITS_MICROUSD[category]:
            raise BudgetExceeded("reservation exceeds the category's entire daily allocation")
        if (
            not isinstance(request.max_output_tokens, int)
            or isinstance(request.max_output_tokens, bool)
            or not 1 <= request.max_output_tokens <= route.max_output_tokens
        ):
            raise ModelGatewayError("max_output_tokens exceeds the allowlisted route ceiling")
        normalized = self._normalize_messages(request.messages)
        digest_domain = {
            "schema": "dumbmoney.openrouter-request.v1",
            "category": category.value,
            "messages": list(normalized),
            "reserve_microusd": request.reserve_microusd,
            "max_output_tokens": request.max_output_tokens,
            "model": route.model,
            "provider": route.provider,
            "provider_name": route.provider_name,
        }
        return route, normalized, canonical_sha256(digest_domain)

    def _normalize_messages(
        self,
        messages: Sequence[Mapping[str, str]],
    ) -> tuple[dict[str, str], ...]:
        if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence):
            raise ModelGatewayError("messages must be a sequence")
        if not 1 <= len(messages) <= 128:
            raise ModelGatewayError("messages must contain between 1 and 128 items")
        total_chars = 0
        normalized: list[dict[str, str]] = []
        for index, message in enumerate(messages):
            if not isinstance(message, Mapping) or set(message) != {"role", "content"}:
                raise SensitivePayloadRejected(
                    f"message {index} must contain only text role and content"
                )
            role = message.get("role")
            content = message.get("content")
            if role not in {"system", "user", "assistant"}:
                raise SensitivePayloadRejected(f"message {index} has a forbidden role")
            if not isinstance(content, str) or not content:
                raise ModelGatewayError(f"message {index} content must be non-empty text")
            if "\x00" in content:
                raise SensitivePayloadRejected("NUL bytes are forbidden")
            total_chars += len(content)
            if total_chars > self.config.max_prompt_characters:
                raise ModelGatewayError("prompt exceeds the configured character ceiling")
            _reject_sensitive_text(content)
            normalized.append({"role": role, "content": content})
        return tuple(normalized)

    def _reserve(
        self,
        request: GatewayRequest,
        route: ModelRoute,
        request_digest: str,
        day: str,
        now: datetime,
    ) -> None:
        category = BudgetCategory(request.category)
        with self._transaction():
            day_state = self._db.execute(
                "SELECT paused, pause_reason FROM model_daily_state WHERE utc_day = ?",
                (day,),
            ).fetchone()
            if day_state is not None and bool(day_state["paused"]):
                raise GatewayPaused(str(day_state["pause_reason"] or "UTC budget day is paused"))
            active = self._db.execute(
                """
                SELECT request_id, state FROM model_requests
                WHERE state IN ('RESERVED','DISPATCHING','AMBIGUOUS') LIMIT 1
                """
            ).fetchone()
            if active is not None:
                raise GatewayPaused(
                    f"single-flight lock is held by {active['request_id']} ({active['state']})"
                )
            category_total = int(
                self._db.execute(
                    """
                    SELECT COALESCE(SUM(CASE WHEN state = 'SETTLED' THEN actual_microusd
                                             ELSE reserve_microusd END), 0) AS total
                    FROM model_requests WHERE utc_day = ? AND category = ?
                    """,
                    (day, category.value),
                ).fetchone()["total"]
            )
            if category_total + request.reserve_microusd > CATEGORY_LIMITS_MICROUSD[category]:
                raise BudgetExceeded(f"{category.value} daily allocation is exhausted")
            totals = self._db.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN state = 'SETTLED' THEN actual_microusd
                                      ELSE 0 END), 0) AS settled_total
                FROM model_requests WHERE utc_day = ?
                """,
                (day,),
            ).fetchone()
            server_row = self._db.execute(
                """
                SELECT server_usage_microusd, local_settled_at_attestation_microusd
                FROM model_daily_state WHERE utc_day = ?
                """,
                (day,),
            ).fetchone()
            settled_total = int(totals["settled_total"])
            if server_row is None or server_row["server_usage_microusd"] is None:
                effective_total = settled_total
            else:
                effective_total = (
                    int(server_row["server_usage_microusd"])
                    + max(
                        0,
                        settled_total
                        - int(server_row["local_settled_at_attestation_microusd"] or 0),
                    )
                )
            if effective_total + request.reserve_microusd > DAILY_CAP_MICROUSD:
                raise BudgetExceeded("global $10 UTC daily budget is exhausted")
            timestamp = format_utc(now)
            self._db.execute(
                """
                INSERT INTO model_requests(
                    request_id, utc_day, category, request_digest, model_digest,
                    provider_digest, reserve_microusd, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'RESERVED', ?, ?)
                """,
                (
                    request.request_id,
                    day,
                    category.value,
                    request_digest,
                    canonical_sha256(route.model),
                    canonical_sha256(
                        {"slug": route.provider, "name": route.provider_name}
                    ),
                    request.reserve_microusd,
                    timestamp,
                    timestamp,
                ),
            )
            self._append_journal(
                event_type="RESERVED",
                request_id=request.request_id,
                request_digest=request_digest,
                state=RequestState.RESERVED,
                at=now,
                cost_microusd=request.reserve_microusd,
            )

    def _load_secret(self) -> str:
        secret = self._secret_loader()
        if not isinstance(secret, str) or not secret or len(secret) > 1_024:
            raise ConfigurationError("secret loader returned an invalid value")
        if any(ch in secret for ch in "\r\n"):
            raise ConfigurationError("secret loader returned a multiline value")
        return secret

    def _attest_key(self, secret: str, day: str) -> int:
        started = self._now()
        response = self._transport.request_json(
            method="GET",
            url=KEY_STATUS_URL,
            headers=self._headers(secret),
            body=None,
            timeout_seconds=self.config.request_timeout_seconds,
            max_response_bytes=self.config.max_response_bytes,
        )
        completed = self._now()
        if response.status != 200:
            raise AttestationError(f"key status returned HTTP {response.status}")
        if completed.date().isoformat() != day:
            raise AttestationError("UTC day rolled during key attestation")
        if (completed - started).total_seconds() > self.config.attestation_max_age_seconds:
            raise AttestationError("key status attestation was not fresh")
        data = response.body.get("data")
        if not isinstance(data, Mapping):
            raise AttestationError("key status is missing data")
        if data.get("label") != self.config.expected_key_label:
            raise AttestationError("key label does not identify the dedicated DumbMoney key")
        if _decimal(data.get("limit"), "key.limit") != Decimal("10"):
            raise AttestationError("dedicated key limit must be exactly $10")
        if data.get("limit_reset") != "daily":
            raise AttestationError("dedicated key limit_reset must be daily")
        if data.get("include_byok_in_limit") is not True:
            raise AttestationError("dedicated key must include BYOK usage in its limit")
        usage = _decimal(data.get("usage_daily"), "key.usage_daily")
        byok_usage = _decimal(data.get("byok_usage_daily"), "key.byok_usage_daily")
        if usage < 0 or byok_usage < 0:
            raise AttestationError("key usage cannot be negative")
        server_usage = _decimal_to_microusd(usage + byok_usage, "key daily usage")
        if server_usage > DAILY_CAP_MICROUSD:
            raise AttestationError("server usage exceeds the dedicated key cap")
        attestation_digest = canonical_sha256(
            {
                "schema": "dumbmoney.openrouter-key-attestation.v1",
                "label_digest": canonical_sha256(self.config.expected_key_label),
                "limit_microusd": DAILY_CAP_MICROUSD,
                "limit_reset": "daily",
                "include_byok_in_limit": True,
                "server_usage_microusd": server_usage,
                "received_at": format_utc(completed),
            }
        )
        with self._transaction():
            local_settled = int(
                self._db.execute(
                    """
                    SELECT COALESCE(SUM(actual_microusd), 0) AS total
                    FROM model_requests WHERE utc_day = ? AND state = 'SETTLED'
                    """,
                    (day,),
                ).fetchone()["total"]
            )
            prior = self._db.execute(
                "SELECT * FROM model_daily_state WHERE utc_day = ?",
                (day,),
            ).fetchone()
            if prior is not None and bool(prior["paused"]):
                raise GatewayPaused(str(prior["pause_reason"] or "UTC budget day is paused"))
            if prior is not None and prior["attested_at"] is not None:
                previous_server = int(prior["server_usage_microusd"])
                previous_local = int(prior["local_settled_at_attestation_microusd"])
                expected = previous_server + (local_settled - previous_local)
                # Integer micro-USD rounds each charge upward.  One micro-dollar
                # is the maximum harmless aggregate quantization difference.
                if abs(server_usage - expected) > 1:
                    raise AttestationError("server/local usage drift detected")
            self._db.execute(
                """
                INSERT INTO model_daily_state(
                    utc_day, server_usage_microusd,
                    local_settled_at_attestation_microusd, attested_at,
                    attestation_digest, paused, pause_reason
                ) VALUES (?, ?, ?, ?, ?, 0, NULL)
                ON CONFLICT(utc_day) DO UPDATE SET
                    server_usage_microusd = excluded.server_usage_microusd,
                    local_settled_at_attestation_microusd =
                        excluded.local_settled_at_attestation_microusd,
                    attested_at = excluded.attested_at,
                    attestation_digest = excluded.attestation_digest
                """,
                (
                    day,
                    server_usage,
                    local_settled,
                    format_utc(completed),
                    attestation_digest,
                ),
            )
        return server_usage

    def _validate_reserved_against_server(
        self,
        request_id: str,
        day: str,
        server_usage: int,
    ) -> None:
        with self._transaction():
            row = self._db.execute(
                "SELECT reserve_microusd, state FROM model_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None or row["state"] != RequestState.RESERVED.value:
                raise GatewayPaused("reservation is no longer dispatchable")
            if server_usage + int(row["reserve_microusd"]) > DAILY_CAP_MICROUSD:
                raise BudgetExceeded("fresh server usage leaves insufficient daily budget")

    def _wire_request(
        self,
        route: ModelRoute,
        messages: Sequence[Mapping[str, str]],
        max_output_tokens: int,
    ) -> Mapping[str, Any]:
        return {
            "model": route.model,
            "messages": [dict(message) for message in messages],
            "max_tokens": max_output_tokens,
            "stream": False,
            "provider": {
                "only": [route.provider],
                "order": [route.provider],
                "allow_fallbacks": False,
                "require_parameters": True,
                "data_collection": "deny",
                "zdr": True,
                "max_price": dict(route.max_price),
            },
        }

    def _mark_dispatching(self, request_id: str) -> None:
        now = self._now()
        with self._transaction():
            row = self._db.execute(
                "SELECT request_digest, state FROM model_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None or row["state"] != RequestState.RESERVED.value:
                raise GatewayPaused("reservation is not dispatchable")
            self._db.execute(
                "UPDATE model_requests SET state = 'DISPATCHING', updated_at = ? WHERE request_id = ?",
                (format_utc(now), request_id),
            )
            self._append_journal(
                event_type="DISPATCHING",
                request_id=request_id,
                request_digest=str(row["request_digest"]),
                state=RequestState.DISPATCHING,
                at=now,
            )

    def _validate_completion_response(
        self,
        response: HttpResponse,
        route: ModelRoute,
        max_output_tokens: int,
    ) -> tuple[str, Decimal]:
        generation_id = response.body.get("id")
        if not isinstance(generation_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", generation_id
        ):
            raise ReconciliationError("completion response is missing a valid generation id")
        if response.body.get("model") != route.model:
            raise ReconciliationError("completion response model differs from the allowlisted model")
        top_level_provider = response.body.get("provider")
        if top_level_provider is not None and top_level_provider != route.provider_name:
            raise ReconciliationError("completion response provider differs from the allowlisted provider")
        self._validate_router_metadata(response.body.get("openrouter_metadata"), route)
        usage = response.body.get("usage")
        if not isinstance(usage, Mapping) or "cost" not in usage:
            raise ReconciliationError("completion response is missing usage.cost")
        cost = _decimal(usage.get("cost"), "usage.cost")
        if cost < 0:
            raise ReconciliationError("usage.cost cannot be negative")
        completion_tokens = usage.get("completion_tokens")
        if (
            not isinstance(completion_tokens, int)
            or isinstance(completion_tokens, bool)
            or completion_tokens < 0
            or completion_tokens > max_output_tokens
        ):
            raise ReconciliationError("completion token count is missing or exceeds the request ceiling")
        choices = response.body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ReconciliationError("completion response is missing bounded output")
        for choice in choices:
            if not isinstance(choice, Mapping):
                raise ReconciliationError("completion choice must be an object")
            message = choice.get("message")
            if not isinstance(message, Mapping) or message.get("role") != "assistant":
                raise ReconciliationError("completion choice is missing an assistant message")
            if set(message) - {"role", "content", "refusal"}:
                raise ReconciliationError("completion returned tools, files, or unsupported modalities")
            if not isinstance(message.get("content"), str):
                raise ReconciliationError("completion output must be text")
        return generation_id, cost

    def _validate_router_metadata(self, value: Any, route: ModelRoute) -> None:
        if not isinstance(value, Mapping):
            raise ReconciliationError("completion response is missing router metadata")
        if value.get("requested") != route.model or value.get("strategy") != "direct":
            raise ReconciliationError("router metadata does not describe the direct allowlisted model")
        if value.get("attempt") != 1:
            raise ReconciliationError("router metadata indicates a retry or fallback")
        endpoints = value.get("endpoints")
        available = endpoints.get("available") if isinstance(endpoints, Mapping) else None
        if not isinstance(available, list):
            raise ReconciliationError("router metadata is missing endpoint evidence")
        selected = [
            endpoint
            for endpoint in available
            if isinstance(endpoint, Mapping) and endpoint.get("selected") is True
        ]
        if len(selected) != 1:
            raise ReconciliationError("router metadata does not identify one selected endpoint")
        if (
            selected[0].get("provider") != route.provider_name
            or selected[0].get("model") != route.model
        ):
            raise ReconciliationError("selected endpoint differs from the allowlisted route")
        attempts = value.get("attempts")
        if attempts is not None:
            if not isinstance(attempts, list) or len(attempts) != 1:
                raise ReconciliationError("router metadata contains unexpected attempts")
            attempt = attempts[0]
            if (
                not isinstance(attempt, Mapping)
                or attempt.get("provider") != route.provider_name
                or attempt.get("model") != route.model
                or attempt.get("status") != 200
            ):
                raise ReconciliationError("router attempt differs from the allowlisted route")
        pipeline = value.get("pipeline", [])
        if not isinstance(pipeline, list):
            raise ReconciliationError("router pipeline metadata is invalid")
        for stage in pipeline:
            if not isinstance(stage, Mapping):
                raise ReconciliationError("router pipeline stage is invalid")
            if stage.get("type") in {"plugin", "server_tools"} or stage.get("name") in {
                "web-search",
                "file-parser",
                "server-tools",
            }:
                raise ReconciliationError("router metadata reports a forbidden plugin or tool")

    def _generation_cost(
        self,
        response: HttpResponse,
        expected_id: str,
        route: ModelRoute,
    ) -> Decimal:
        data = response.body.get("data")
        if not isinstance(data, Mapping):
            raise ReconciliationError("generation lookup is missing data")
        if data.get("id") != expected_id:
            raise ReconciliationError("generation lookup returned a different id")
        if data.get("provider_name") != route.provider_name:
            raise ReconciliationError("generation provider differs from the allowlisted provider")
        generation_model = data.get("model")
        if generation_model is not None and generation_model != route.model:
            raise ReconciliationError("generation model differs from the allowlisted model")
        if "total_cost" not in data:
            raise ReconciliationError("generation lookup is missing total_cost")
        cost = _decimal(data.get("total_cost"), "generation.total_cost")
        if cost < 0:
            raise ReconciliationError("generation.total_cost cannot be negative")
        return cost

    def _settle(
        self,
        request_id: str,
        generation_id: str,
        actual_microusd: int,
        response_digest: str,
        response_body: Mapping[str, Any],
    ) -> CompletionResult:
        now = self._now()
        with self._transaction():
            row = self._db.execute(
                """
                SELECT request_digest, reserve_microusd, state
                FROM model_requests WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
            if row is None or row["state"] != RequestState.DISPATCHING.value:
                raise ReconciliationError("dispatch state changed before settlement")
            if actual_microusd > int(row["reserve_microusd"]):
                raise ReconciliationError("actual cost exceeds the locked reservation")
            duplicate = self._db.execute(
                "SELECT request_id FROM model_requests WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
            if duplicate is not None and duplicate["request_id"] != request_id:
                raise ReconciliationError("generation id was already used by another request")
            generation_digest = canonical_sha256(generation_id)
            self._db.execute(
                """
                UPDATE model_requests
                SET state = 'SETTLED', actual_microusd = ?, generation_id = ?,
                    generation_digest = ?, response_digest = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (
                    actual_microusd,
                    generation_id,
                    generation_digest,
                    response_digest,
                    format_utc(now),
                    request_id,
                ),
            )
            self._append_journal(
                event_type="SETTLED",
                request_id=request_id,
                request_digest=str(row["request_digest"]),
                state=RequestState.SETTLED,
                at=now,
                cost_microusd=actual_microusd,
                generation_id=generation_id,
                response_digest=response_digest,
            )
        return CompletionResult(
            request_id=request_id,
            state=RequestState.SETTLED,
            generation_id=generation_id,
            actual_cost_microusd=actual_microusd,
            response_digest=response_digest,
            response=response_body,
            replayed=False,
        )

    def _existing_request(self, request_id: str) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self._db.execute(
                "SELECT * FROM model_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone(),
        )

    def _duplicate_result(self, row: sqlite3.Row, request_digest: str) -> CompletionResult:
        if row["request_digest"] != request_digest:
            raise DuplicateRequest("request_id was reused with different request content")
        state = RequestState(str(row["state"]))
        if state is not RequestState.SETTLED:
            raise DuplicateRequest(f"request_id is already {state.value}; it will not be resent")
        return CompletionResult(
            request_id=str(row["request_id"]),
            state=state,
            generation_id=cast(str | None, row["generation_id"]),
            actual_cost_microusd=(
                int(row["actual_microusd"]) if row["actual_microusd"] is not None else None
            ),
            response_digest=cast(str | None, row["response_digest"]),
            response=None,
            replayed=True,
        )

    def _lock_and_pause(self, request_id: str, day: str, reason: str) -> None:
        now = self._now()
        try:
            with self._transaction():
                row = self._db.execute(
                    "SELECT request_digest, state FROM model_requests WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if row is None or row["state"] == RequestState.SETTLED.value:
                    return
                self._db.execute(
                    """
                    UPDATE model_requests
                    SET state = 'AMBIGUOUS', failure_reason_digest = ?, updated_at = ?
                    WHERE request_id = ?
                    """,
                    (canonical_sha256(reason), format_utc(now), request_id),
                )
                self._pause_day(day, reason)
                self._append_journal(
                    event_type="AMBIGUOUS",
                    request_id=request_id,
                    request_digest=str(row["request_digest"]),
                    state=RequestState.AMBIGUOUS,
                    at=now,
                    reason=reason,
                )
        except sqlite3.Error as exc:
            raise GatewayPaused("failed to persist the fail-closed pause") from exc

    def _pause_day(self, day: str, reason: str) -> None:
        self._db.execute(
            """
            INSERT INTO model_daily_state(utc_day, paused, pause_reason)
            VALUES (?, 1, ?)
            ON CONFLICT(utc_day) DO UPDATE SET
                paused = 1,
                pause_reason = excluded.pause_reason
            """,
            (day, reason[:240]),
        )

    def _append_journal(
        self,
        *,
        event_type: str,
        request_id: str,
        request_digest: str,
        state: RequestState,
        at: datetime,
        cost_microusd: int | None = None,
        generation_id: str | None = None,
        response_digest: str | None = None,
        reason: str | None = None,
    ) -> None:
        previous = self._db.execute(
            "SELECT event_id FROM model_journal ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        event: dict[str, Any] = {
            "schema": "dumbmoney.model-gateway-journal.v1",
            "event_type": event_type,
            "request_id_digest": canonical_sha256(request_id),
            "request_digest": request_digest,
            "state": state.value,
            "at": format_utc(at),
            "previous_event_id": str(previous["event_id"]) if previous is not None else None,
        }
        if cost_microusd is not None:
            event["cost_microusd"] = cost_microusd
        if generation_id is not None:
            event["generation_id_digest"] = canonical_sha256(generation_id)
        if response_digest is not None:
            event["response_digest"] = response_digest
        if reason is not None:
            event["reason_digest"] = canonical_sha256(reason)
        event_id = canonical_sha256(event)
        event["event_id"] = event_id
        canonical_event = canonical_json_bytes(event).decode("utf-8")
        self._db.execute(
            """
            INSERT INTO model_journal(event_id, previous_event_id, canonical_event)
            VALUES (?, ?, ?)
            """,
            (
                event_id,
                str(previous["event_id"]) if previous is not None else None,
                canonical_event,
            ),
        )

    def _headers(self, secret: str) -> Mapping[str, str]:
        return {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Cache-Control": "no-cache, no-store",
            "Pragma": "no-cache",
            "X-OpenRouter-Cache": "false",
            "X-OpenRouter-Metadata": "enabled",
            "X-OpenRouter-Title": "DumbMoney Model Gateway",
        }

    def _assert_dispatch_window(self, day: str) -> None:
        now = self._now()
        if now.date().isoformat() != day:
            raise AttestationError("UTC day rolled before dispatch")
        self._assert_not_near_midnight(now)

    def _assert_same_day(self, day: str) -> None:
        if self._now().date().isoformat() != day:
            raise ReconciliationError("UTC day rolled during dispatch or reconciliation")

    def _assert_not_near_midnight(self, now: datetime) -> None:
        next_day = datetime.combine(
            now.date() + timedelta(days=1),
            time.min,
            tzinfo=timezone.utc,
        )
        seconds_remaining = (next_day - now).total_seconds()
        if seconds_remaining <= self.config.near_midnight_fence_seconds:
            raise GatewayPaused("new reservations are fenced near UTC midnight")

    def _now(self) -> datetime:
        return self._normalize_time(self._clock())

    @staticmethod
    def _normalize_time(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConfigurationError("clock must return an aware datetime")
        return value.astimezone(timezone.utc)

    class _Transaction:
        def __init__(self, database: sqlite3.Connection) -> None:
            self.database = database

        def __enter__(self) -> None:
            self.database.execute("BEGIN IMMEDIATE")

        def __exit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> Literal[False]:
            if exc_type is None:
                self.database.execute("COMMIT")
            else:
                self.database.execute("ROLLBACK")
            return False

    def _transaction(self) -> "_Transaction":
        return self._Transaction(self._db)


def _require_concrete_slug(value: str, context: str, *, require_slash: bool) -> None:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._:-]*)*",
        value,
    ):
        raise ConfigurationError(f"{context} must be a concrete OpenRouter slug")
    if require_slash and "/" not in value:
        raise ConfigurationError(f"{context} must include an owner/model pair")
    lowered = value.casefold()
    if any(marker in lowered for marker in ("*", "auto", "random", "fallback")):
        raise ConfigurationError(f"{context} cannot use a router, wildcard, or fallback")


_SENSITIVE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bsk-or-v1-[A-Za-z0-9_-]+\b",
        r"\bsk-[A-Za-z0-9_-]{20,}\b",
        r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
        r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b",
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----",
        r"\b[0-9a-fA-F]{40,}\b",
        r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}\b",
        r"\b(?:api[-_ ]?key|private[-_ ]?key|client[-_ ]?secret|access[-_ ]?token|"
        r"refresh[-_ ]?token|password|passphrase|credential)s?\b",
        r"\b(?:robinhood|kalshi)\b",
        r"\b(?:broker|brokerage|trading)\s+account\b",
        r"\baccount\s+(?:number|id|identifier|balance|holdings?|positions?|orders?)\b",
        r"\b(?:routing|aba)\s+number\b",
    )
)


def _reject_sensitive_text(value: str) -> None:
    # Public content identities are expected in research prompts.  Remove only
    # explicit, structurally labelled SHA-256 values before applying the broad
    # secret heuristics; unlabeled long hexadecimal material remains blocked.
    scanned = re.sub(
        r"\bsha256:[0-9a-f]{64}\b",
        "sha256:<public-content-digest>",
        value,
    )
    scanned = re.sub(
        r'(?<="content_sha256":")[0-9a-f]{64}(?=")',
        "<public-content-digest>",
        scanned,
    )
    scanned = re.sub(
        r"\bdumbmoney\.(?:[a-z][a-z0-9_]*\.)*v[0-9]+\b",
        "dumbmoney.<public-schema>",
        scanned,
    )
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.search(scanned):
            raise SensitivePayloadRejected(
                "prompt rejected by the broker/account/credential data boundary"
            )
    # This is defense in depth, not a proof that content is non-sensitive.
    # Mixed-alphabet, high-entropy tokens are rejected because many secrets do
    # not carry a recognizable vendor prefix.
    for token in re.findall(
        r"(?<![A-Za-z0-9])[A-Za-z0-9_+/=.-]{32,}(?![A-Za-z0-9])",
        scanned,
    ):
        classes = sum(
            (
                any(char.islower() for char in token),
                any(char.isupper() for char in token),
                any(char.isdigit() for char in token),
                any(char in "_+/=.-" for char in token),
            )
        )
        if classes >= 3 and _shannon_entropy(token) >= 4.0:
            raise SensitivePayloadRejected(
                "prompt rejected by the high-entropy credential defense"
            )


def _shannon_entropy(value: str) -> float:
    counts: dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    length = len(value)
    return -sum(
        (count / length) * math.log2(count / length)
        for count in counts.values()
    )


def _decimal(value: Any, context: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ReconciliationError(f"{context} is missing or invalid")
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ReconciliationError(f"{context} is not a decimal") from exc
    if not decimal_value.is_finite():
        raise ReconciliationError(f"{context} must be finite")
    return decimal_value


def _decimal_to_microusd(value: Decimal, context: str) -> int:
    if value < 0:
        raise ReconciliationError(f"{context} cannot be negative")
    micros = (value * MICRO_USD_PER_USD).to_integral_value(rounding=ROUND_CEILING)
    if micros > Decimal(2**63 - 1):
        raise ReconciliationError(f"{context} exceeds the supported range")
    return int(micros)


def _micro_to_conservative_cents(value: int) -> int:
    if value <= 0:
        return 0
    return (value + 9_999) // 10_000


def _json_digest_domain(value: Any) -> Any:
    """Convert decoded Decimal values to exact strings for canonical hashing."""

    if isinstance(value, Decimal):
        return {"$decimal": format(value, "f")}
    if isinstance(value, Mapping):
        return {str(key): _json_digest_domain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_digest_domain(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise ReconciliationError(f"response contains unsupported JSON value {type(value).__name__}")


def _safe_reason(exc: Exception) -> str:
    if isinstance(exc, BudgetExceeded):
        return "BUDGET_ATTESTATION_FAILED"
    if isinstance(exc, AttestationError):
        return "KEY_ATTESTATION_FAILED"
    if isinstance(exc, ReconciliationError):
        return "COST_RECONCILIATION_FAILED"
    if isinstance(exc, TransportError):
        return "TRANSPORT_OUTCOME_UNCERTAIN"
    if isinstance(exc, GatewayPaused):
        return "GATEWAY_PAUSED"
    return f"FAIL_CLOSED_{type(exc).__name__.upper()[:80]}"


__all__ = [
    "AttestationError",
    "BudgetCategory",
    "BudgetExceeded",
    "CATEGORY_LIMITS_MICROUSD",
    "CHAT_COMPLETIONS_URL",
    "CompletionResult",
    "ConfigurationError",
    "DAILY_CAP_MICROUSD",
    "WindowsCredentialSecretLoader",
    "GENERATION_URL",
    "GatewayConfig",
    "GatewayPaused",
    "GatewayRequest",
    "GatewayStatus",
    "HttpResponse",
    "JsonTransport",
    "KEY_STATUS_URL",
    "MICRO_USD_PER_USD",
    "ModelGatewayError",
    "ModelRoute",
    "OpenRouterModelGateway",
    "ReconciliationError",
    "RequestState",
    "SensitivePayloadRejected",
    "StdlibHttpsTransport",
    "TransportError",
]
