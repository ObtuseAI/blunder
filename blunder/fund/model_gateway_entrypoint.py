"""Private, fail-closed Windows service runner for the DumbMoney model gateway.

The runner accepts only a content-pinned public configuration. Secret values
come from Windows Credential Manager under the service identity. The HTTP
surface binds literal loopback, authenticates completion and status requests,
and never exposes a general OpenAI-compatible proxy.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import hmac
import importlib
import ipaddress
import json
import os
import re
import signal
import socket
import sys
import threading
import time
import uuid
from collections.abc import Mapping as ABCMapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, Literal, Mapping, Protocol, Sequence, cast
from urllib.parse import urlsplit

from blunder.fund.canonical import (
    canonical_json_bytes,
    canonical_sha256,
    loads_strict_json,
    require_digest,
    require_identifier,
)
from blunder.fund.contracts import ReadinessDescriptorV1, SignedEnvelopeV1
from blunder.fund.crypto import Ed25519Signer, decode_base64url
from blunder.fund.model_gateway import (
    AttestationError,
    BudgetCategory,
    BudgetExceeded,
    CompletionResult,
    ConfigurationError,
    DuplicateRequest,
    GatewayConfig,
    GatewayPaused,
    GatewayRequest,
    JsonTransport,
    ModelGatewayError,
    ModelRoute,
    OpenRouterModelGateway,
    ReconciliationError,
    SensitivePayloadRejected,
    StdlibHttpsTransport,
    TransportError,
)


CONFIG_SCHEMA = "dumbmoney.model-gateway-runner-config.v1"
REQUEST_SCHEMA = "dumbmoney.model-completion-request.v1"
RESPONSE_SCHEMA = "dumbmoney.model-completion-result.v1"
SERVICE_STATUS_SCHEMA = "dumbmoney.model-gateway-service-status.v1"
API_ERROR_SCHEMA = "dumbmoney.model-gateway-api-error.v1"
READINESS_SOURCE: Literal["DumbMoneyModelGateway"] = "DumbMoneyModelGateway"
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_CONFIG_PATH = Path(
    r"C:\ProgramData\DumbMoney\config\model-gateway-runner.v1.json"
)
DATABASE_FILE_NAME = "model-gateway.sqlite3"
INSTANCE_LOCK_FILE_NAME = "model-gateway.instance.lock"

HEALTH_LIVE_PATH = "/health/live"
HEALTH_READY_PATH = "/health/ready"
STATUS_PATH = "/v1/status"
COMPLETION_PATH = "/v1/completions"

_CREDENTIAL_TARGET_NAMES = {
    "openrouter_api_key",
    "gateway_signing_seed",
    "client_bearer_token",
}
_EXPECTED_CREDENTIAL_TARGETS = {
    "openrouter_api_key": "credential-target:DumbMoney/OpenRouterApiKey",
    "gateway_signing_seed": "credential-target:DumbMoney/ModelGatewaySigner",
    "client_bearer_token": "credential-target:DumbMoney/ModelGatewayClientToken",
}
_CONFIG_FIELDS = {
    "schema",
    "data_root",
    "readiness_path",
    "gateway_public_key_path",
    "fund_lock_path",
    "service_manifest_path",
    "bind_port",
    "release_id",
    "fund_lock_sha256",
    "service_manifest_sha256",
    "gateway_public_key_sha256",
    "readiness_ttl_seconds",
    "request_body_max_bytes",
    "max_active_requests",
    "client_socket_timeout_seconds",
    "request_timeout_seconds",
    "attestation_max_age_seconds",
    "near_midnight_fence_seconds",
    "max_prompt_characters",
    "max_response_bytes",
    "expected_key_label",
    "credential_targets",
    "routes",
}
_ROUTE_FIELDS = {
    "model",
    "provider",
    "provider_name",
    "max_output_tokens",
    "max_price",
}
_PLACEHOLDER_RE = re.compile(
    r"(?:TO_BE_RESOLVED|CHANGEME|PLACEHOLDER|EXAMPLE_ONLY)",
    re.IGNORECASE,
)


class GatewayRunnerError(RuntimeError):
    """Base class for service-runner startup and lifecycle failures."""


class CredentialProviderError(GatewayRunnerError):
    """A required OS-protected credential could not be loaded safely."""


class CredentialProvider(Protocol):
    def read_bytes(self, target: str) -> bytes:
        """Return one generic credential blob without logging it."""


class WindowsCredentialManager:
    """Read-only Windows generic-credential provider."""

    CRED_TYPE_GENERIC = 1

    def read_bytes(self, target: str) -> bytes:
        require_identifier(target, "credential target")
        if os.name != "nt":
            raise CredentialProviderError(
                "Windows Credential Manager is required for the production runner"
            )

        from ctypes import wintypes

        class FileTime(ctypes.Structure):
            _fields_ = [
                ("low_datetime", wintypes.DWORD),
                ("high_datetime", wintypes.DWORD),
            ]

        class CredentialAttribute(ctypes.Structure):
            _fields_ = [
                ("keyword", wintypes.LPWSTR),
                ("flags", wintypes.DWORD),
                ("value_size", wintypes.DWORD),
                ("value", ctypes.POINTER(ctypes.c_ubyte)),
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
                ("attributes", ctypes.POINTER(CredentialAttribute)),
                ("target_alias", wintypes.LPWSTR),
                ("user_name", wintypes.LPWSTR),
            ]

        credential_pointer = ctypes.POINTER(Credential)()
        advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        cred_read = advapi32.CredReadW
        cred_read.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(Credential)),
        ]
        cred_read.restype = wintypes.BOOL
        cred_free = advapi32.CredFree
        cred_free.argtypes = [ctypes.c_void_p]
        cred_free.restype = None
        if not cred_read(
            target,
            self.CRED_TYPE_GENERIC,
            0,
            ctypes.byref(credential_pointer),
        ):
            error_code = ctypes.get_last_error()
            raise CredentialProviderError(
                f"credential target could not be read; winerror={error_code}"
            )
        try:
            credential = credential_pointer.contents
            size = int(credential.credential_blob_size)
            if size <= 0 or size > 16_384:
                raise CredentialProviderError(
                    "credential target contains an invalid-size blob"
                )
            return ctypes.string_at(credential.credential_blob, size)
        finally:
            cred_free(credential_pointer)


@dataclass
class DataRootLease:
    """Process-lifetime exclusive ownership of the gateway state directory."""

    path: Path
    handle: BinaryIO

    @classmethod
    def acquire(cls, data_root: Path) -> DataRootLease:
        data_root.mkdir(parents=True, exist_ok=True)
        path = data_root / INSTANCE_LOCK_FILE_NAME
        handle = path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise GatewayRunnerError(
                "another model-gateway process already owns the configured data root"
            ) from exc
        return cls(path=path, handle=handle)

    def close(self) -> None:
        if self.handle.closed:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()


def _bounded_int(
    value: object,
    context: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(
            f"{context} must be an integer from {minimum} through {maximum}"
        )
    return value


def _absolute_path(value: object, context: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty absolute path")
    if _PLACEHOLDER_RE.search(value):
        raise ValueError(f"{context} contains an unresolved placeholder")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{context} must be an absolute path")
    return path.resolve()


def _nonplaceholder_text(
    value: object,
    context: str,
    *,
    maximum: int = 128,
) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(character in value for character in "\r\n\x00")
    ):
        raise ValueError(f"{context} must be bounded non-empty text")
    if _PLACEHOLDER_RE.search(value):
        raise ValueError(f"{context} contains an unresolved placeholder")
    return value


def _nonzero_digest(value: object, context: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{context} must be a string")
    digest = require_digest(value, context)
    if digest == "0" * 64:
        raise ValueError(f"{context} must be resolved")
    return digest


def _parse_routes(value: object) -> Mapping[BudgetCategory, ModelRoute]:
    if not isinstance(value, dict) or set(value) != {
        category.value for category in BudgetCategory
    }:
        raise ValueError(
            "routes must define exactly research, evaluation, and operations"
        )
    routes: dict[BudgetCategory, ModelRoute] = {}
    for category in BudgetCategory:
        raw = value[category.value]
        if not isinstance(raw, dict) or set(raw) != _ROUTE_FIELDS:
            raise ValueError(
                f"routes.{category.value} must contain exactly {sorted(_ROUTE_FIELDS)}"
            )
        model = _nonplaceholder_text(
            raw["model"],
            f"routes.{category.value}.model",
            maximum=255,
        )
        provider = _nonplaceholder_text(
            raw["provider"],
            f"routes.{category.value}.provider",
            maximum=255,
        )
        provider_name = _nonplaceholder_text(
            raw["provider_name"],
            f"routes.{category.value}.provider_name",
            maximum=128,
        )
        max_output_tokens = _bounded_int(
            raw["max_output_tokens"],
            f"routes.{category.value}.max_output_tokens",
            minimum=1,
            maximum=32_768,
        )
        raw_price = raw["max_price"]
        if not isinstance(raw_price, dict):
            raise TypeError(f"routes.{category.value}.max_price must be an object")
        max_price: dict[str, int] = {}
        for name, price in raw_price.items():
            if not isinstance(name, str):
                raise TypeError(
                    f"routes.{category.value}.max_price keys must be strings"
                )
            max_price[name] = _bounded_int(
                price,
                f"routes.{category.value}.max_price.{name}",
                minimum=0,
                maximum=1_000_000_000,
            )
        routes[category] = ModelRoute(
            model=model,
            provider=provider,
            provider_name=provider_name,
            max_output_tokens=max_output_tokens,
            max_price=max_price,
        )
    return MappingProxyType(routes)


@dataclass(frozen=True)
class ModelGatewayRunnerConfig:
    """Strict public configuration for one private model-gateway instance."""

    data_root: Path
    readiness_path: Path
    gateway_public_key_path: Path
    fund_lock_path: Path
    service_manifest_path: Path
    bind_port: int
    release_id: str
    fund_lock_sha256: str
    service_manifest_sha256: str
    gateway_public_key_sha256: str
    runner_config_sha256: str
    readiness_ttl_seconds: int
    request_body_max_bytes: int
    max_active_requests: int
    client_socket_timeout_seconds: int
    request_timeout_seconds: int
    attestation_max_age_seconds: int
    near_midnight_fence_seconds: int
    max_prompt_characters: int
    max_response_bytes: int
    expected_key_label: str
    credential_targets: Mapping[str, str]
    routes: Mapping[BudgetCategory, ModelRoute]

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
    ) -> ModelGatewayRunnerConfig:
        if set(value) != _CONFIG_FIELDS:
            raise ValueError(
                "model-gateway runner config keys are invalid; "
                f"missing={sorted(_CONFIG_FIELDS - set(value))}; "
                f"unknown={sorted(set(value) - _CONFIG_FIELDS)}"
            )
        if value["schema"] != CONFIG_SCHEMA:
            raise ValueError(
                f"unsupported model-gateway runner config schema: {value['schema']}"
            )
        bind_port = value["bind_port"]
        if (
            isinstance(bind_port, bool)
            or not isinstance(bind_port, int)
            or (bind_port != 0 and not 1_024 <= bind_port <= 65_535)
        ):
            raise ValueError(
                "bind_port must be 0 or an integer from 1024 through 65535"
            )

        raw_targets = value["credential_targets"]
        if (
            not isinstance(raw_targets, dict)
            or set(raw_targets) != _CREDENTIAL_TARGET_NAMES
        ):
            raise ValueError(
                "credential_targets must define exactly openrouter_api_key, "
                "gateway_signing_seed, and client_bearer_token"
            )
        targets: dict[str, str] = {}
        for name, target in raw_targets.items():
            if not isinstance(target, str):
                raise TypeError(f"credential_targets.{name} must be a string")
            targets[name] = require_identifier(
                target,
                f"credential_targets.{name}",
            )
            if target != _EXPECTED_CREDENTIAL_TARGETS[name]:
                raise ValueError(
                    f"credential_targets.{name} must use the dedicated "
                    "DumbMoney service target"
                )
        if len(set(targets.values())) != len(targets):
            raise ValueError("credential target names must be distinct")

        readiness_ttl = _bounded_int(
            value["readiness_ttl_seconds"],
            "readiness_ttl_seconds",
            minimum=10,
            maximum=120,
        )
        request_timeout = _bounded_int(
            value["request_timeout_seconds"],
            "request_timeout_seconds",
            minimum=1,
            maximum=120,
        )
        attestation_max_age = _bounded_int(
            value["attestation_max_age_seconds"],
            "attestation_max_age_seconds",
            minimum=1,
            maximum=120,
        )
        near_midnight_fence = _bounded_int(
            value["near_midnight_fence_seconds"],
            "near_midnight_fence_seconds",
            minimum=60,
            maximum=3_600,
        )

        result = cls(
            data_root=_absolute_path(value["data_root"], "data_root"),
            readiness_path=_absolute_path(
                value["readiness_path"],
                "readiness_path",
            ),
            gateway_public_key_path=_absolute_path(
                value["gateway_public_key_path"],
                "gateway_public_key_path",
            ),
            fund_lock_path=_absolute_path(
                value["fund_lock_path"],
                "fund_lock_path",
            ),
            service_manifest_path=_absolute_path(
                value["service_manifest_path"],
                "service_manifest_path",
            ),
            bind_port=bind_port,
            release_id=_nonplaceholder_text(value["release_id"], "release_id"),
            fund_lock_sha256=_nonzero_digest(
                value["fund_lock_sha256"],
                "fund_lock_sha256",
            ),
            service_manifest_sha256=_nonzero_digest(
                value["service_manifest_sha256"],
                "service_manifest_sha256",
            ),
            gateway_public_key_sha256=_nonzero_digest(
                value["gateway_public_key_sha256"],
                "gateway_public_key_sha256",
            ),
            runner_config_sha256=canonical_sha256(value),
            readiness_ttl_seconds=readiness_ttl,
            request_body_max_bytes=_bounded_int(
                value["request_body_max_bytes"],
                "request_body_max_bytes",
                minimum=1_024,
                maximum=2_000_000,
            ),
            max_active_requests=_bounded_int(
                value["max_active_requests"],
                "max_active_requests",
                minimum=1,
                maximum=16,
            ),
            client_socket_timeout_seconds=_bounded_int(
                value["client_socket_timeout_seconds"],
                "client_socket_timeout_seconds",
                minimum=1,
                maximum=30,
            ),
            request_timeout_seconds=request_timeout,
            attestation_max_age_seconds=attestation_max_age,
            near_midnight_fence_seconds=near_midnight_fence,
            max_prompt_characters=_bounded_int(
                value["max_prompt_characters"],
                "max_prompt_characters",
                minimum=1,
                maximum=200_000,
            ),
            max_response_bytes=_bounded_int(
                value["max_response_bytes"],
                "max_response_bytes",
                minimum=1_024,
                maximum=2_000_000,
            ),
            expected_key_label=_nonplaceholder_text(
                value["expected_key_label"],
                "expected_key_label",
                maximum=128,
            ),
            credential_targets=MappingProxyType(targets),
            routes=_parse_routes(value["routes"]),
        )
        # Constructing the underlying config here validates cross-field timing
        # constraints before any credential is accessed or state path is created.
        result.gateway_config()
        return result

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        expected_file_sha256: str,
    ) -> ModelGatewayRunnerConfig:
        if not path.is_absolute():
            raise ValueError("--config must be an absolute path")
        require_digest(expected_file_sha256, "--config-sha256")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError("model-gateway runner config cannot be read") from exc
        observed = hashlib.sha256(raw).hexdigest()
        if not hmac.compare_digest(observed, expected_file_sha256):
            raise ValueError(
                "model-gateway runner config does not match --config-sha256"
            )
        parsed = loads_strict_json(raw, "model-gateway runner config")
        return replace(
            cls.from_dict(parsed),
            runner_config_sha256=observed,
        )

    def gateway_config(self) -> GatewayConfig:
        return GatewayConfig(
            database_path=self.data_root / DATABASE_FILE_NAME,
            expected_key_label=self.expected_key_label,
            routes=self.routes,
            request_timeout_seconds=self.request_timeout_seconds,
            attestation_max_age_seconds=self.attestation_max_age_seconds,
            near_midnight_fence_seconds=self.near_midnight_fence_seconds,
            max_prompt_characters=self.max_prompt_characters,
            max_response_bytes=self.max_response_bytes,
        )


def _verified_file_bytes(path: Path, expected_digest: str, context: str) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{context} cannot be read") from exc
    observed = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(observed, expected_digest):
        raise ValueError(f"{context} does not match its pinned SHA-256 digest")
    return raw


def _verified_json(
    path: Path,
    expected_digest: str,
    context: str,
) -> Mapping[str, object]:
    return loads_strict_json(
        _verified_file_bytes(path, expected_digest, context),
        context,
    )


def _validate_release_files(config: ModelGatewayRunnerConfig) -> bytes:
    fund_lock = _verified_json(
        config.fund_lock_path,
        config.fund_lock_sha256,
        "fund lock",
    )
    if (
        fund_lock.get("schema_version") != "dumbmoney.fund-lock.v1"
        or fund_lock.get("deployment_scope") != "PRIVATE_LOCAL_WINDOWS"
        or fund_lock.get("public_distribution") is not False
    ):
        raise ValueError("fund lock is not a private-local DumbMoney v1 lock")

    service_manifest = _verified_json(
        config.service_manifest_path,
        config.service_manifest_sha256,
        "service manifest",
    )
    services = service_manifest.get("services")
    if not isinstance(services, list):
        raise ValueError("service manifest is missing services")
    matches = [
        item
        for item in services
        if isinstance(item, dict) and item.get("name") == READINESS_SOURCE
    ]
    if len(matches) != 1:
        raise ValueError("service manifest must contain one model-gateway service")
    gateway_service = matches[0]
    if gateway_service.get("broker_authority") != "NONE":
        raise ValueError(
            "model-gateway service manifest must declare broker authority NONE"
        )
    allowlist = gateway_service.get("network_allowlist")
    if not isinstance(allowlist, list) or set(allowlist) != {
        "loopback",
        "https://openrouter.ai",
    }:
        raise ValueError(
            "model-gateway network allowlist must contain only loopback and OpenRouter"
        )
    bindings = gateway_service.get("config_bindings")
    if (
        not isinstance(bindings, dict)
        or bindings.get("openrouter_credential_target")
        != config.credential_targets["openrouter_api_key"]
        or bindings.get("gateway_signer_target")
        != config.credential_targets["gateway_signing_seed"]
        or bindings.get("client_bearer_token_target")
        != config.credential_targets["client_bearer_token"]
    ):
        raise ValueError(
            "service manifest model-gateway credential targets do not match runner config"
        )

    public_key_raw = _verified_file_bytes(
        config.gateway_public_key_path,
        config.gateway_public_key_sha256,
        "gateway public key",
    )
    try:
        public_key_text = public_key_raw.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ValueError(
            "gateway public key file must contain ASCII base64url"
        ) from exc
    public_key = decode_base64url(public_key_text, "gateway public key file")
    if len(public_key) != 32:
        raise ValueError(
            "gateway public key file must contain exactly 32 decoded bytes"
        )
    return public_key


def _load_text_credential(
    provider: CredentialProvider,
    target: str,
    context: str,
    *,
    minimum: int,
    maximum: int,
) -> str:
    raw = provider.read_bytes(target)
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CredentialProviderError(f"{context} must contain UTF-8 bytes") from exc
    if not minimum <= len(value) <= maximum or any(
        character.isspace() or ord(character) < 0x20 for character in value
    ):
        raise CredentialProviderError(
            f"{context} must be {minimum}-{maximum} non-whitespace UTF-8 characters"
        )
    return value


class GatewayRuntimeHealth:
    """Small nonblocking health cache kept outside the gateway SQLite lock."""

    def __init__(self, initial_budget: Mapping[str, Any]) -> None:
        self._lock = threading.Lock()
        self._request_active = False
        self._budget_bytes = canonical_json_bytes(initial_budget)
        self._last_error_code: str | None = None

    def begin_request(self) -> bool:
        with self._lock:
            if self._request_active:
                return False
            self._request_active = True
            return True

    def finish_request(
        self,
        budget: Mapping[str, Any],
        *,
        error_code: str | None,
    ) -> None:
        with self._lock:
            self._budget_bytes = canonical_json_bytes(budget)
            self._request_active = False
            self._last_error_code = error_code

    def fail_internal(self) -> None:
        with self._lock:
            self._request_active = False
            self._last_error_code = "INTERNAL_STATUS_REFRESH_FAILED"

    def snapshot(self) -> Mapping[str, object]:
        with self._lock:
            budget_bytes = self._budget_bytes
            active = self._request_active
            last_error = self._last_error_code
        budget = dict(loads_strict_json(budget_bytes, "cached gateway budget"))
        paused = budget.get("research_paused") is True
        unresolved = budget.get("unresolved_count")
        unresolved_count = (
            unresolved
            if isinstance(unresolved, int) and not isinstance(unresolved, bool)
            else 0
        )
        ready = (
            not active
            and not paused
            and unresolved_count == 0
            and last_error != "INTERNAL_STATUS_REFRESH_FAILED"
        )
        return MappingProxyType(
            {
                "schema": SERVICE_STATUS_SCHEMA,
                "health": {
                    "status": "READY" if ready else "BLOCKED",
                    "request_active": active,
                    "last_error_code": last_error,
                },
                "authority": {
                    "broker": "NONE",
                    "mode": "OFFLINE",
                    "execution_enabled": False,
                },
                "budget": budget,
            }
        )


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: bytes
    headers: Mapping[str, str] = MappingProxyType({})


def _json_response(
    status: int,
    body: Mapping[str, object],
    *,
    headers: Mapping[str, str] | None = None,
) -> ApiResponse:
    return ApiResponse(
        status=status,
        body=canonical_json_bytes(body),
        headers=MappingProxyType(dict(headers or {})),
    )


def _api_error(
    status: int,
    code: str,
    *,
    authenticate: bool = False,
) -> ApiResponse:
    headers = (
        {"www-authenticate": 'Bearer realm="DumbMoneyModelGateway"'}
        if authenticate
        else {}
    )
    return _json_response(
        status,
        {
            "schema": API_ERROR_SCHEMA,
            "code": code,
            "retryable": False,
        },
        headers=headers,
    )


def _completion_outputs(result: CompletionResult) -> tuple[Mapping[str, str], ...]:
    response = result.response
    if response is None:
        return ()
    choices = response.get("choices")
    if not isinstance(choices, list):
        return ()
    outputs: list[Mapping[str, str]] = []
    for choice in choices:
        if not isinstance(choice, ABCMapping):
            continue
        message = choice.get("message")
        if not isinstance(message, ABCMapping):
            continue
        content = message.get("content")
        if isinstance(content, str):
            outputs.append(MappingProxyType({"role": "assistant", "content": content}))
    return tuple(outputs)


class ModelGatewayApi:
    """Typed, authenticated application surface for the local gateway."""

    def __init__(
        self,
        gateway: OpenRouterModelGateway,
        health: GatewayRuntimeHealth,
        client_bearer_token: str,
        *,
        request_body_max_bytes: int,
    ) -> None:
        self.gateway = gateway
        self.health = health
        self._client_bearer_token = client_bearer_token
        self.request_body_max_bytes = request_body_max_bytes

    def _authenticated(self, authorization: str | None) -> bool:
        if authorization is None or not authorization.startswith("Bearer "):
            return False
        presented = authorization[7:]
        return bool(presented) and hmac.compare_digest(
            presented,
            self._client_bearer_token,
        )

    def _refresh_health(self, *, error_code: str | None) -> None:
        try:
            budget = self.gateway.redacted_control_snapshot()
        except Exception:
            self.health.fail_internal()
            return
        self.health.finish_request(budget, error_code=error_code)

    def handle(
        self,
        method: str,
        target: str,
        body: bytes,
        *,
        authorization: str | None,
    ) -> ApiResponse:
        parsed = urlsplit(target)
        if parsed.query or parsed.fragment or parsed.path != target:
            return _api_error(400, "INVALID_TARGET")
        path = parsed.path

        if method == "GET" and path == HEALTH_LIVE_PATH:
            return _json_response(
                200,
                {
                    "schema": "dumbmoney.health.v1",
                    "service_name": READINESS_SOURCE,
                    "status": "LIVE",
                },
            )
        if method == "GET" and path == HEALTH_READY_PATH:
            snapshot = self.health.snapshot()
            ready = cast(Mapping[str, object], snapshot["health"])["status"] == "READY"
            return _json_response(
                200 if ready else 503,
                {
                    "schema": "dumbmoney.health.v1",
                    "service_name": READINESS_SOURCE,
                    "status": "READY" if ready else "BLOCKED",
                },
            )

        if path in {STATUS_PATH, COMPLETION_PATH} and not self._authenticated(
            authorization
        ):
            return _api_error(401, "UNAUTHORIZED", authenticate=True)

        if method == "GET" and path == STATUS_PATH:
            return _json_response(200, self.health.snapshot())

        if method == "POST" and path == COMPLETION_PATH:
            if len(body) > self.request_body_max_bytes:
                return _api_error(413, "REQUEST_BODY_TOO_LARGE")
            try:
                raw = loads_strict_json(body, "model completion request")
                request = self._parse_completion_request(raw)
            except (TypeError, ValueError, ModelGatewayError):
                return _api_error(400, "INVALID_REQUEST")
            if not self.health.begin_request():
                return _api_error(429, "GATEWAY_BUSY")
            error_code: str | None = None
            try:
                result = self.gateway.complete(request)
            except SensitivePayloadRejected:
                error_code = "SENSITIVE_PAYLOAD_REJECTED"
                return _api_error(422, error_code)
            except BudgetExceeded:
                error_code = "BUDGET_EXCEEDED"
                return _api_error(429, error_code)
            except DuplicateRequest:
                error_code = "REQUEST_UNRESOLVED"
                return _api_error(409, error_code)
            except GatewayPaused:
                error_code = "GATEWAY_PAUSED"
                return _api_error(503, error_code)
            except AttestationError:
                error_code = "PROVIDER_ATTESTATION_FAILED"
                return _api_error(503, error_code)
            except (ReconciliationError, TransportError):
                error_code = "PROVIDER_OUTCOME_UNCERTAIN"
                return _api_error(503, error_code)
            except ModelGatewayError:
                error_code = "REQUEST_REJECTED"
                return _api_error(400, error_code)
            except Exception:
                error_code = "INTERNAL_FAILURE"
                return _api_error(500, error_code)
            finally:
                self._refresh_health(error_code=error_code)

            outputs = _completion_outputs(result)
            return _json_response(
                200,
                {
                    "schema": RESPONSE_SCHEMA,
                    "request_id": result.request_id,
                    "state": result.state.value,
                    "generation_id": result.generation_id,
                    "actual_cost_microusd": result.actual_cost_microusd,
                    "response_digest": result.response_digest,
                    "output_available": result.response is not None,
                    "outputs": outputs,
                    "replayed": result.replayed,
                },
            )

        if path in {
            HEALTH_LIVE_PATH,
            HEALTH_READY_PATH,
            STATUS_PATH,
            COMPLETION_PATH,
        }:
            return _api_error(405, "METHOD_NOT_ALLOWED")
        return _api_error(404, "NOT_FOUND")

    @staticmethod
    def _parse_completion_request(
        value: Mapping[str, object],
    ) -> GatewayRequest:
        expected = {
            "schema",
            "request_id",
            "category",
            "messages",
            "reserve_microusd",
            "max_output_tokens",
        }
        if set(value) != expected:
            raise ValueError("completion request fields are invalid")
        if value["schema"] != REQUEST_SCHEMA:
            raise ValueError("completion request schema is invalid")
        request_id = value["request_id"]
        category = value["category"]
        messages = value["messages"]
        reserve = value["reserve_microusd"]
        max_tokens = value["max_output_tokens"]
        if not isinstance(request_id, str):
            raise TypeError("request_id must be a string")
        if not isinstance(category, str):
            raise TypeError("category must be a string")
        if not isinstance(messages, list):
            raise TypeError("messages must be an array")
        if not isinstance(reserve, int) or isinstance(reserve, bool):
            raise TypeError("reserve_microusd must be an integer")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool):
            raise TypeError("max_output_tokens must be an integer")
        normalized_messages: list[Mapping[str, str]] = []
        for message in messages:
            if (
                not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or not all(isinstance(item, str) for item in message.values())
            ):
                raise TypeError("messages must contain role/content string objects")
            normalized_messages.append(cast(Mapping[str, str], message))
        return GatewayRequest(
            request_id=request_id,
            category=BudgetCategory(category),
            messages=tuple(normalized_messages),
            reserve_microusd=reserve,
            max_output_tokens=max_tokens,
        )


class BoundedLoopbackServer(ThreadingHTTPServer):
    """Threaded HTTP server with a hard worker ceiling and bounded sockets."""

    daemon_threads = False
    block_on_close = True
    allow_reuse_address = False
    request_queue_size = 8

    def __init__(
        self,
        bind_port: int,
        api: ModelGatewayApi,
        *,
        max_active_requests: int,
        socket_timeout_seconds: int,
    ) -> None:
        self.gateway_api = api
        self.socket_timeout_seconds = socket_timeout_seconds
        self._worker_slots = threading.BoundedSemaphore(max_active_requests)
        super().__init__((LOOPBACK_HOST, bind_port), ModelGatewayRequestHandler)

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        request, client_address = cast(
            tuple[socket.socket, tuple[str, int]],
            super().get_request(),
        )
        request.settimeout(self.socket_timeout_seconds)
        return request, client_address

    def process_request(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        if not self._worker_slots.acquire(blocking=False):
            body = canonical_json_bytes(
                {
                    "schema": API_ERROR_SCHEMA,
                    "code": "SERVER_BUSY",
                    "retryable": False,
                }
            )
            response = (
                b"HTTP/1.1 503 Service Unavailable\r\n"
                b"Content-Type: application/json; charset=utf-8\r\n"
                b"Cache-Control: no-store\r\n"
                b"Connection: close\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                + body
            )
            try:
                cast(socket.socket, request).sendall(response)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._worker_slots.release()
            raise

    def process_request_thread(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


class ModelGatewayRequestHandler(BaseHTTPRequestHandler):
    """Transport adapter with strict Host, body, and method handling."""

    protocol_version = "HTTP/1.1"
    server_version = "DumbMoneyModelGateway"
    sys_version = ""

    def log_message(self, _format: str, *_args: object) -> None:
        # Request targets and authorization headers must never enter default logs.
        return

    def send_error(
        self,
        code: int,
        message: str | None = None,
        explain: str | None = None,
    ) -> None:
        del message, explain
        self._write_response(_api_error(code, "MALFORMED_HTTP_REQUEST"))

    def _gateway_server(self) -> BoundedLoopbackServer:
        return cast(BoundedLoopbackServer, self.server)

    def _transport_allowed(self) -> bool:
        try:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return False
        except ValueError:
            return False
        host_values = self.headers.get_all("Host", failobj=[])
        expected_host = f"{LOOPBACK_HOST}:{self._gateway_server().server_address[1]}"
        return len(host_values) == 1 and hmac.compare_digest(
            host_values[0],
            expected_host,
        )

    def _authorization(self) -> str | None:
        values = self.headers.get_all("Authorization", failobj=[])
        if len(values) != 1:
            return None
        return values[0]

    def _read_post_body(self) -> bytes | None:
        if self.headers.get("Transfer-Encoding") is not None:
            self._write_response(_api_error(400, "TRANSFER_ENCODING_FORBIDDEN"))
            return None
        content_type = self.headers.get("Content-Type")
        if content_type not in {
            "application/json",
            "application/json; charset=utf-8",
        }:
            self._write_response(_api_error(415, "CONTENT_TYPE_UNSUPPORTED"))
            return None
        values = self.headers.get_all("Content-Length", failobj=[])
        if len(values) != 1 or not values[0].isdigit():
            self._write_response(_api_error(411, "CONTENT_LENGTH_REQUIRED"))
            return None
        length = int(values[0])
        maximum = self._gateway_server().gateway_api.request_body_max_bytes
        if length <= 0:
            self._write_response(_api_error(400, "REQUEST_BODY_REQUIRED"))
            return None
        if length > maximum:
            self._write_response(_api_error(413, "REQUEST_BODY_TOO_LARGE"))
            return None
        try:
            body = self.rfile.read(length)
        except (OSError, TimeoutError):
            self._write_response(_api_error(408, "REQUEST_BODY_TIMEOUT"))
            return None
        if len(body) != length:
            self._write_response(_api_error(400, "REQUEST_BODY_TRUNCATED"))
            return None
        return body

    def _dispatch(self, method: str) -> None:
        if not self._transport_allowed():
            self._write_response(_api_error(421, "LOOPBACK_HOST_REQUIRED"))
            return
        authorization = self._authorization()
        if method == "POST":
            if not self._gateway_server().gateway_api._authenticated(authorization):
                self._write_response(_api_error(401, "UNAUTHORIZED", authenticate=True))
                return
            body = self._read_post_body()
            if body is None:
                return
        else:
            if self.headers.get("Transfer-Encoding") is not None:
                self._write_response(_api_error(400, "REQUEST_BODY_FORBIDDEN"))
                return
            length = self.headers.get("Content-Length")
            if length not in {None, "0"}:
                self._write_response(_api_error(400, "REQUEST_BODY_FORBIDDEN"))
                return
            body = b""
        response = self._gateway_server().gateway_api.handle(
            method,
            self.path,
            body,
            authorization=authorization,
        )
        self._write_response(response)

    def _write_response(self, response: ApiResponse) -> None:
        self.close_connection = True
        self.send_response(response.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-DumbMoney-Retryable", "false")
        self.send_header("Connection", "close")
        for name, value in response.headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(response.body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                # A disconnected caller cannot change the already-durable
                # gateway outcome and must not trigger a provider retry.
                return

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_OPTIONS(self) -> None:
        self._dispatch("OPTIONS")


class Clock(Protocol):
    def __call__(self) -> datetime:
        """Return one timezone-aware current instant."""


@dataclass
class ModelGatewayService:
    """One fully constructed gateway, server, signer, and readiness lifecycle."""

    config: ModelGatewayRunnerConfig
    gateway: OpenRouterModelGateway
    health: GatewayRuntimeHealth
    server: BoundedLoopbackServer
    signer: Ed25519Signer
    instance_id: str
    data_root_lease: DataRootLease
    clock: Clock
    _readiness_sequence: int = 0
    _closed: bool = False

    @property
    def endpoint(self) -> tuple[str, int]:
        host, port = cast(tuple[str, int], self.server.server_address)
        return host, port

    def _readiness_envelope(self, observed_at: datetime) -> SignedEnvelopeV1:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("readiness time must include an explicit timezone")
        now = observed_at.astimezone(timezone.utc)
        valid_until = now + timedelta(seconds=self.config.readiness_ttl_seconds)
        snapshot = self.health.snapshot()
        health = cast(Mapping[str, object], snapshot["health"])
        budget = cast(Mapping[str, object], snapshot["budget"])
        host, port = self.endpoint
        body = ReadinessDescriptorV1(
            service_name=READINESS_SOURCE,
            release_id=self.config.release_id,
            instance_id=self.instance_id,
            process_id=os.getpid(),
            generation=0,
            observed_at=now,
            valid_until=valid_until,
            endpoint={
                "transport": "http",
                "host": host,
                "port": port,
                "base_path": "/",
            },
            fund_lock_sha256=self.config.fund_lock_sha256,
            service_manifest_sha256=self.config.service_manifest_sha256,
            authority={
                "broker": "NONE",
                "mode": "OFFLINE",
                "execution_enabled": False,
            },
            health={
                "status": health["status"],
                "request_active": health["request_active"],
                "last_error_code": health["last_error_code"],
                "runner_config_sha256": self.config.runner_config_sha256,
                "gateway_public_key_sha256": self.config.gateway_public_key_sha256,
                "daily_budget_microusd": budget["daily_budget_microusd"],
                "research_paused": budget["research_paused"],
                "unresolved_count": budget["unresolved_count"],
            },
            capabilities=(
                "authenticated-local-completions",
                "fixed-60-20-20-budget",
                "no-broker-authority",
                "redacted-budget-status",
            ),
        )
        self._readiness_sequence += 1
        return SignedEnvelopeV1.issue(
            body,
            source_id=READINESS_SOURCE,
            source_sequence=self._readiness_sequence,
            correlation_id=f"readiness-{self.instance_id}",
            causation_id=None,
            nonce=canonical_sha256(
                [
                    "model-gateway-readiness",
                    self.instance_id,
                    self._readiness_sequence,
                    now.isoformat(),
                ]
            ),
            not_before=now,
            expires_at=valid_until,
            signer=self.signer,
        )

    def write_readiness(
        self,
        observed_at: datetime | None = None,
    ) -> SignedEnvelopeV1:
        envelope = self._readiness_envelope(observed_at or self.clock())
        destination = self.config.readiness_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        payload = canonical_json_bytes(envelope.to_dict()) + b"\n"
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return envelope

    def serve(self, stop_event: threading.Event) -> None:
        self.server.timeout = 0.25
        refresh_interval = max(1.0, self.config.readiness_ttl_seconds / 2)
        next_readiness = 0.0
        while not stop_event.is_set():
            monotonic = time.monotonic()
            if monotonic >= next_readiness:
                self.write_readiness()
                next_readiness = monotonic + refresh_interval
            self.server.handle_request()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.server.server_close()
        finally:
            try:
                self.gateway.close()
            finally:
                self.data_root_lease.close()

    def __enter__(self) -> ModelGatewayService:
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        self.close()


def build_model_gateway_service(
    config: ModelGatewayRunnerConfig,
    credential_provider: CredentialProvider,
    *,
    transport: JsonTransport,
    clock: Clock | None = None,
) -> ModelGatewayService:
    """Construct all resources or close every partial resource on failure."""

    public_key = _validate_release_files(config)
    lease = DataRootLease.acquire(config.data_root)
    gateway: OpenRouterModelGateway | None = None
    server: BoundedLoopbackServer | None = None
    try:
        seed = credential_provider.read_bytes(
            config.credential_targets["gateway_signing_seed"]
        )
        if len(seed) != 32:
            raise CredentialProviderError(
                "gateway signing credential must contain one raw 32-byte Ed25519 seed"
            )
        signer = Ed25519Signer.from_private_bytes(seed)
        if not hmac.compare_digest(signer.public_key_bytes, public_key):
            raise CredentialProviderError(
                "gateway signing credential does not match the pinned public key"
            )

        def load_openrouter_key() -> str:
            return _load_text_credential(
                credential_provider,
                config.credential_targets["openrouter_api_key"],
                "OpenRouter API-key credential",
                minimum=16,
                maximum=512,
            )

        # Credential existence and shape are startup gates. Provider attestation
        # remains the gateway's fresh, immediately-before-dispatch GET check.
        openrouter_key = load_openrouter_key()
        client_token = _load_text_credential(
            credential_provider,
            config.credential_targets["client_bearer_token"],
            "model-gateway client bearer credential",
            minimum=32,
            maximum=512,
        )
        if hmac.compare_digest(openrouter_key, client_token):
            raise CredentialProviderError(
                "OpenRouter and local client credentials must be distinct"
            )
        openrouter_key = ""

        active_clock = clock or (lambda: datetime.now(timezone.utc))
        gateway = OpenRouterModelGateway(
            config.gateway_config(),
            transport=transport,
            secret_loader=load_openrouter_key,
            clock=active_clock,
        )
        initial_budget = gateway.redacted_control_snapshot()
        health = GatewayRuntimeHealth(initial_budget)
        api = ModelGatewayApi(
            gateway,
            health,
            client_token,
            request_body_max_bytes=config.request_body_max_bytes,
        )
        server = BoundedLoopbackServer(
            config.bind_port,
            api,
            max_active_requests=config.max_active_requests,
            socket_timeout_seconds=config.client_socket_timeout_seconds,
        )
        return ModelGatewayService(
            config=config,
            gateway=gateway,
            health=health,
            server=server,
            signer=signer,
            instance_id=str(uuid.uuid4()),
            data_root_lease=lease,
            clock=active_clock,
        )
    except Exception:
        if server is not None:
            server.server_close()
        if gateway is not None:
            gateway.close()
        lease.close()
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dumbmoney-model-gateway",
        description="Run the private authenticated DumbMoney model gateway.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Absolute public runner config path; never contains secret values.",
    )
    parser.add_argument(
        "--config-sha256",
        required=True,
        help="Pinned SHA-256 of the exact public runner config bytes.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)
    try:
        config = ModelGatewayRunnerConfig.load(
            args.config,
            expected_file_sha256=args.config_sha256,
        )
        with build_model_gateway_service(
            config,
            WindowsCredentialManager(),
            transport=StdlibHttpsTransport(),
        ) as service:
            readiness = service.write_readiness()
            host, port = service.endpoint
            print(
                json.dumps(
                    {
                        "schema": "dumbmoney.model-gateway-ready.v1",
                        "service_name": READINESS_SOURCE,
                        "host": host,
                        "port": port,
                        "process_id": os.getpid(),
                        "instance_id": service.instance_id,
                        "readiness_path": str(config.readiness_path),
                        "signer_key_id": readiness.signer_key_id,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                flush=True,
            )
            service.serve(stop_event)
        return 0
    except (
        CredentialProviderError,
        GatewayRunnerError,
        ConfigurationError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        print(
            "dumbmoney-model-gateway startup failed safely: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
