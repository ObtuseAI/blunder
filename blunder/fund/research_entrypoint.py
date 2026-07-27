"""Private Windows runner for the candidate-only DumbMoney Research Mesh.

The public config contains only paths, digests, loopback ports, and Credential
Manager target names.  The runner refuses broker or model-provider credential
fields by exact-shape validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Literal, Mapping, Sequence, cast
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from blunder.fund.canonical import (
    canonical_json_bytes,
    canonical_sha256,
    loads_strict_json,
    require_digest,
    require_identifier,
)
from blunder.fund.contracts import ReadinessDescriptorV1, SignedEnvelopeV1
from blunder.fund.crypto import Ed25519Signer, decode_base64url
from blunder.fund.entrypoint import (
    CredentialProvider,
    CredentialProviderError,
    DataRootLease,
    WindowsCredentialManager,
)
from blunder.fund.model_spool import (
    ModelGatewayClient,
    ModelGatewayIdentity,
    ModelGatewayReadinessVerifier,
    ModelGatewayTransport,
    ModelSpoolCycleResult,
    ModelSpoolRelay,
    ModelSpoolState,
    UrllibModelGatewayTransport,
)
from blunder.fund.research_mesh import (
    AllocatorCoreClient,
    CoreHttpResponse,
    CoreIdentity,
    CoreReadinessVerifier,
    CoreTransport,
    CoreTransportError,
    CycleResult,
    ResearchMesh,
    ResearchMeshState,
)


CONFIG_SCHEMA = "dumbmoney.research-mesh-runner-config.v1"
READINESS_SOURCE: Literal["DumbMoneyResearchMesh"] = "DumbMoneyResearchMesh"
DEFAULT_CONFIG_PATH = Path(
    r"C:\ProgramData\DumbMoney\config\research-mesh-runner.v1.json"
)
MAX_HTTP_BODY_BYTES = 262_144


def _absolute_path(value: object, context: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{context} must be absolute")
    return path.resolve()


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
        raise ValueError(f"{context} must be from {minimum} through {maximum}")
    return value


@dataclass(frozen=True)
class ResearchMeshRunnerConfig:
    data_root: Path
    candidate_inbox: Path
    allocation_inbox: Path
    readiness_path: Path
    research_mesh_public_key_path: Path
    allocator_public_key_path: Path
    core_public_key_path: Path
    core_readiness_path: Path
    model_request_inbox: Path
    model_response_outbox: Path
    model_outcome_outbox: Path
    model_gateway_readiness_path: Path
    model_gateway_public_key_path: Path
    fund_lock_path: Path
    service_manifest_path: Path
    bind_port: int
    release_id: str
    research_mesh_public_key_sha256: str
    allocator_public_key_sha256: str
    core_public_key_sha256: str
    model_gateway_public_key_sha256: str
    model_gateway_runner_config_sha256: str
    fund_lock_sha256: str
    service_manifest_sha256: str
    readiness_ttl_seconds: int
    processing_interval_milliseconds: int
    core_timeout_seconds: int
    model_gateway_timeout_seconds: int
    max_input_bytes: int
    model_request_max_bytes: int
    model_response_max_bytes: int
    model_prompt_max_characters: int
    model_max_output_tokens: int
    model_max_reserve_microusd: int
    credential_targets: Mapping[str, str]
    runner_config_sha256: str

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
    ) -> "ResearchMeshRunnerConfig":
        expected = {
            "schema",
            "data_root",
            "candidate_inbox",
            "allocation_inbox",
            "readiness_path",
            "research_mesh_public_key_path",
            "allocator_public_key_path",
            "core_public_key_path",
            "core_readiness_path",
            "model_request_inbox",
            "model_response_outbox",
            "model_outcome_outbox",
            "model_gateway_readiness_path",
            "model_gateway_public_key_path",
            "fund_lock_path",
            "service_manifest_path",
            "bind_port",
            "release_id",
            "research_mesh_public_key_sha256",
            "allocator_public_key_sha256",
            "core_public_key_sha256",
            "model_gateway_public_key_sha256",
            "model_gateway_runner_config_sha256",
            "fund_lock_sha256",
            "service_manifest_sha256",
            "readiness_ttl_seconds",
            "processing_interval_milliseconds",
            "core_timeout_seconds",
            "model_gateway_timeout_seconds",
            "max_input_bytes",
            "model_request_max_bytes",
            "model_response_max_bytes",
            "model_prompt_max_characters",
            "model_max_output_tokens",
            "model_max_reserve_microusd",
            "credential_targets",
        }
        if set(value) != expected:
            raise ValueError(
                "research mesh runner config keys are invalid; "
                f"missing={sorted(expected - set(value))}; "
                f"unknown={sorted(set(value) - expected)}"
            )
        if value["schema"] != CONFIG_SCHEMA:
            raise ValueError(
                f"unsupported Research Mesh config schema: {value['schema']}"
            )
        paths = {
            name: _absolute_path(value[name], name)
            for name in (
                "data_root",
                "candidate_inbox",
                "allocation_inbox",
                "readiness_path",
                "research_mesh_public_key_path",
                "allocator_public_key_path",
                "core_public_key_path",
                "core_readiness_path",
                "model_request_inbox",
                "model_response_outbox",
                "model_outcome_outbox",
                "model_gateway_readiness_path",
                "model_gateway_public_key_path",
                "fund_lock_path",
                "service_manifest_path",
            )
        }
        spool_and_mesh_paths = (
            paths["candidate_inbox"],
            paths["allocation_inbox"],
            paths["model_request_inbox"],
            paths["model_response_outbox"],
            paths["model_outcome_outbox"],
        )
        if len(set(spool_and_mesh_paths)) != len(spool_and_mesh_paths):
            raise ValueError("Research Mesh inbox and model spool paths must be distinct")
        if paths["readiness_path"].parent in {
            *spool_and_mesh_paths,
        }:
            raise ValueError("readiness artifact cannot be written into an inbox")
        bind_port = value["bind_port"]
        if (
            isinstance(bind_port, bool)
            or not isinstance(bind_port, int)
            or not 0 <= bind_port <= 65_535
        ):
            raise ValueError("bind_port must be from 0 through 65535")
        release_id = value["release_id"]
        if not isinstance(release_id, str) or not release_id.strip():
            raise ValueError("release_id must be non-empty")
        digests: dict[str, str] = {}
        for name in (
            "research_mesh_public_key_sha256",
            "allocator_public_key_sha256",
            "core_public_key_sha256",
            "model_gateway_public_key_sha256",
            "model_gateway_runner_config_sha256",
            "fund_lock_sha256",
            "service_manifest_sha256",
        ):
            digest = value[name]
            if not isinstance(digest, str):
                raise TypeError(f"{name} must be a string")
            digests[name] = require_digest(digest, name)
        readiness_ttl = value["readiness_ttl_seconds"]
        if (
            isinstance(readiness_ttl, bool)
            or not isinstance(readiness_ttl, int)
            or not 10 <= readiness_ttl <= 120
        ):
            raise ValueError("readiness_ttl_seconds must be from 10 through 120")
        processing_interval = value["processing_interval_milliseconds"]
        if (
            isinstance(processing_interval, bool)
            or not isinstance(processing_interval, int)
            or not 100 <= processing_interval <= 60_000
        ):
            raise ValueError(
                "processing_interval_milliseconds must be from 100 through 60000"
            )
        timeout = value["core_timeout_seconds"]
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not 1 <= timeout <= 30
        ):
            raise ValueError("core_timeout_seconds must be from 1 through 30")
        max_input = value["max_input_bytes"]
        if (
            isinstance(max_input, bool)
            or not isinstance(max_input, int)
            or not 4_096 <= max_input <= 16_777_216
        ):
            raise ValueError(
                "max_input_bytes must be from 4096 through 16777216"
            )
        model_timeout = _bounded_int(
            value["model_gateway_timeout_seconds"],
            "model_gateway_timeout_seconds",
            minimum=1,
            maximum=30,
        )
        model_request_max_bytes = _bounded_int(
            value["model_request_max_bytes"],
            "model_request_max_bytes",
            minimum=4_096,
            maximum=1_048_576,
        )
        model_response_max_bytes = _bounded_int(
            value["model_response_max_bytes"],
            "model_response_max_bytes",
            minimum=4_096,
            maximum=1_048_576,
        )
        model_prompt_max_characters = _bounded_int(
            value["model_prompt_max_characters"],
            "model_prompt_max_characters",
            minimum=1,
            maximum=200_000,
        )
        model_max_output_tokens = _bounded_int(
            value["model_max_output_tokens"],
            "model_max_output_tokens",
            minimum=1,
            maximum=8_192,
        )
        model_max_reserve_microusd = _bounded_int(
            value["model_max_reserve_microusd"],
            "model_max_reserve_microusd",
            minimum=1,
            maximum=6_000_000,
        )
        raw_targets = value["credential_targets"]
        required_targets = {
            "research_mesh_signing_seed",
            "allocator_signing_seed",
            "allocator_bearer_token",
            "model_gateway_client_token",
        }
        if not isinstance(raw_targets, dict) or set(raw_targets) != required_targets:
            raise ValueError(
                "credential_targets must define exactly the four internal targets"
            )
        targets: dict[str, str] = {}
        for name, target in raw_targets.items():
            if not isinstance(target, str):
                raise TypeError(f"credential_targets.{name} must be a string")
            targets[name] = require_identifier(
                target,
                f"credential_targets.{name}",
            )
        if len(set(targets.values())) != len(targets):
            raise ValueError("Research Mesh credential target names must be distinct")
        return cls(
            data_root=paths["data_root"],
            candidate_inbox=paths["candidate_inbox"],
            allocation_inbox=paths["allocation_inbox"],
            readiness_path=paths["readiness_path"],
            research_mesh_public_key_path=paths[
                "research_mesh_public_key_path"
            ],
            allocator_public_key_path=paths["allocator_public_key_path"],
            core_public_key_path=paths["core_public_key_path"],
            core_readiness_path=paths["core_readiness_path"],
            model_request_inbox=paths["model_request_inbox"],
            model_response_outbox=paths["model_response_outbox"],
            model_outcome_outbox=paths["model_outcome_outbox"],
            model_gateway_readiness_path=paths["model_gateway_readiness_path"],
            model_gateway_public_key_path=paths[
                "model_gateway_public_key_path"
            ],
            fund_lock_path=paths["fund_lock_path"],
            service_manifest_path=paths["service_manifest_path"],
            bind_port=bind_port,
            release_id=release_id,
            research_mesh_public_key_sha256=digests[
                "research_mesh_public_key_sha256"
            ],
            allocator_public_key_sha256=digests[
                "allocator_public_key_sha256"
            ],
            core_public_key_sha256=digests["core_public_key_sha256"],
            model_gateway_public_key_sha256=digests[
                "model_gateway_public_key_sha256"
            ],
            model_gateway_runner_config_sha256=digests[
                "model_gateway_runner_config_sha256"
            ],
            fund_lock_sha256=digests["fund_lock_sha256"],
            service_manifest_sha256=digests["service_manifest_sha256"],
            readiness_ttl_seconds=readiness_ttl,
            processing_interval_milliseconds=processing_interval,
            core_timeout_seconds=timeout,
            model_gateway_timeout_seconds=model_timeout,
            max_input_bytes=max_input,
            model_request_max_bytes=model_request_max_bytes,
            model_response_max_bytes=model_response_max_bytes,
            model_prompt_max_characters=model_prompt_max_characters,
            model_max_output_tokens=model_max_output_tokens,
            model_max_reserve_microusd=model_max_reserve_microusd,
            credential_targets=MappingProxyType(targets),
            runner_config_sha256=canonical_sha256(value),
        )

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        expected_file_sha256: str,
    ) -> "ResearchMeshRunnerConfig":
        if not path.is_absolute():
            raise ValueError("--config must be an absolute path")
        require_digest(expected_file_sha256, "--config-sha256")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError("Research Mesh config cannot be read") from exc
        observed = hashlib.sha256(raw).hexdigest()
        if observed != expected_file_sha256:
            raise ValueError(
                "Research Mesh config does not match --config-sha256"
            )
        parsed = loads_strict_json(raw, "Research Mesh runner config")
        return replace(
            cls.from_dict(parsed),
            runner_config_sha256=observed,
        )


def _verified_file(path: Path, expected_digest: str, context: str) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{context} cannot be read") from exc
    if hashlib.sha256(raw).hexdigest() != expected_digest:
        raise ValueError(f"{context} does not match its pinned SHA-256 digest")
    return raw


def _read_public_key(
    path: Path,
    expected_digest: str,
    context: str,
) -> bytes:
    raw = _verified_file(path, expected_digest, context)
    try:
        encoded = raw.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ValueError(f"{context} must be ASCII") from exc
    public_key = decode_base64url(encoded, context)
    if len(public_key) != 32:
        raise ValueError(f"{context} must decode to exactly 32 bytes")
    return public_key


def _read_seed(
    provider: CredentialProvider,
    target: str,
    context: str,
) -> bytes:
    seed = provider.read_bytes(target)
    if len(seed) != 32:
        raise CredentialProviderError(
            f"{context} must contain exactly one raw 32-byte Ed25519 seed"
        )
    return seed


def _read_token(
    provider: CredentialProvider,
    target: str,
    context: str,
) -> str:
    raw = provider.read_bytes(target)
    try:
        token = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CredentialProviderError(
            f"{context} must contain UTF-8 bytes"
        ) from exc
    if (
        not 32 <= len(token) <= 512
        or any(character.isspace() for character in token)
    ):
        raise CredentialProviderError(
            f"{context} must be 32-512 non-whitespace characters"
        )
    return token


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        _request: Request,
        _file_pointer: object,
        _code: int,
        _message: str,
        _headers: object,
        _new_url: str,
    ) -> None:
        return None


class UrllibCoreTransport(CoreTransport):
    """Proxy-free, redirect-free bounded transport for literal loopback Core."""

    def __init__(self) -> None:
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    @staticmethod
    def _read_bounded(response: object, maximum: int) -> bytes:
        reader = getattr(response, "read", None)
        if not callable(reader):
            raise CoreTransportError("Core response is not readable")
        raw = cast(bytes, reader(maximum + 1))
        if len(raw) > maximum:
            raise CoreTransportError("Core response exceeds its configured bound")
        return raw

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> CoreHttpResponse:
        if method != "POST":
            raise ValueError("Research Mesh Core transport permits POST only")
        request = Request(
            url,
            data=body,
            headers=dict(headers),
            method=method,
        )
        try:
            response = self._opener.open(request, timeout=timeout_seconds)
        except HTTPError as exc:
            raw = self._read_bounded(exc, max_response_bytes)
            return CoreHttpResponse(
                status=exc.code,
                body=raw,
                headers=MappingProxyType(dict(exc.headers.items())),
            )
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise CoreTransportError("loopback Core request failed") from exc
        try:
            if response.geturl() != url:
                raise CoreTransportError("Core transport followed an unexpected redirect")
            raw = self._read_bounded(response, max_response_bytes)
            return CoreHttpResponse(
                status=cast(int, response.status),
                body=raw,
                headers=MappingProxyType(dict(response.headers.items())),
            )
        finally:
            response.close()


def _make_status_server(
    status_provider: Callable[[], Mapping[str, object]],
    *,
    port: int,
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "DumbMoneyResearchMesh/1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_GET(self) -> None:
            snapshot = dict(status_provider())
            if self.path == "/health/live":
                status = 200
                body: Mapping[str, object] = {
                    "schema": "dumbmoney.health.v1",
                    "status": "LIVE",
                }
            elif self.path == "/health/ready":
                ready = snapshot["health_status"] == "READY"
                status = 200 if ready else 503
                body = {
                    "schema": "dumbmoney.health.v1",
                    "status": "READY" if ready else "NOT_READY",
                    "reason_codes": snapshot["reason_codes"],
                }
            elif self.path == "/api/v1/research/status":
                status = 200
                body = snapshot
            else:
                status = 404
                body = {
                    "schema": "dumbmoney.api-error.v1",
                    "code": "NOT_FOUND",
                }
            payload = json.dumps(
                body,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:
            payload = (
                b'{"code":"METHOD_NOT_ALLOWED",'
                b'"schema":"dumbmoney.api-error.v1"}'
            )
            self.send_response(405)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


@dataclass
class ResearchMeshService:
    config: ResearchMeshRunnerConfig
    mesh: ResearchMesh
    model_relay: ModelSpoolRelay
    state: ResearchMeshState
    readiness_signer: Ed25519Signer
    allocator_signer: Ed25519Signer
    data_root_lease: DataRootLease
    instance_id: str
    server: ThreadingHTTPServer | None = None
    _readiness_sequence: int = 0
    _last_cycle: CycleResult | None = None
    _last_cycle_failed: bool = False
    _last_model_cycle: ModelSpoolCycleResult | None = None
    _last_model_cycle_failed: bool = False
    _status_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )

    def bind(self) -> None:
        if self.server is not None:
            raise RuntimeError("Research Mesh service is already bound")
        self.server = _make_status_server(
            self.status_snapshot,
            port=self.config.bind_port,
        )

    @property
    def endpoint(self) -> tuple[str, int]:
        if self.server is None:
            raise RuntimeError("Research Mesh service is not bound")
        return cast(tuple[str, int], self.server.server_address)

    def _health(self) -> tuple[str, tuple[str, ...]]:
        status = self.mesh.status_snapshot()
        model_status = self.model_relay.status_snapshot()
        reasons: list[str] = []
        if bool(status["blocked_by_ambiguous_submission"]):
            reasons.append("AMBIGUOUS_ALLOCATOR_POST_REQUIRES_RECONCILIATION")
        with self._status_lock:
            cycle = self._last_cycle
            failed = self._last_cycle_failed
            model_cycle = self._last_model_cycle
            model_failed = self._last_model_cycle_failed
        if failed:
            reasons.append("PROCESSING_CYCLE_FAILED")
        if cycle is None:
            reasons.append("FIRST_CYCLE_PENDING")
        elif not cycle.core_ready:
            reasons.append("CORE_NOT_SIGNED_LIVE_READY")
        if model_failed:
            reasons.append("MODEL_RELAY_CYCLE_FAILED")
        if model_cycle is None:
            reasons.append("FIRST_MODEL_RELAY_CYCLE_PENDING")
        elif not model_cycle.gateway_ready:
            reasons.append("MODEL_GATEWAY_NOT_SIGNED_LIVE_READY")
        if bool(model_status["dispatching"]):
            reasons.append("MODEL_COMPLETION_DISPATCHING")
        if bool(model_status["ambiguous"]):
            reasons.append("AMBIGUOUS_MODEL_COMPLETION_REQUIRES_RECONCILIATION")
        if any(reason.startswith("AMBIGUOUS_") for reason in reasons):
            return "BLOCKED", tuple(sorted(set(reasons)))
        if reasons:
            return "DEGRADED", tuple(sorted(set(reasons)))
        return "READY", ()

    def status_snapshot(self) -> Mapping[str, object]:
        health_status, reasons = self._health()
        mesh = dict(self.mesh.status_snapshot())
        with self._status_lock:
            last_cycle = self._last_cycle
            last_model_cycle = self._last_model_cycle
        return MappingProxyType(
            {
                "schema": "dumbmoney.research-mesh-service-status.v1",
                "service_name": READINESS_SOURCE,
                "instance_id": self.instance_id,
                "release_id": self.config.release_id,
                "health_status": health_status,
                "reason_codes": reasons,
                "last_cycle_at": (
                    None
                    if last_cycle is None
                    else last_cycle.observed_at.isoformat()
                ),
                "core_ready": (
                    False if last_cycle is None else last_cycle.core_ready
                ),
                "mesh": mesh,
                "model_relay": dict(self.model_relay.status_snapshot()),
                "last_model_cycle": (
                    None
                    if last_model_cycle is None
                    else {
                        "requests_dispatched": last_model_cycle.requests_dispatched,
                        "gateway_ready": last_model_cycle.gateway_ready,
                        "responses_completed": last_model_cycle.responses_completed,
                        "requests_rejected": last_model_cycle.requests_rejected,
                        "requests_ambiguous": last_model_cycle.requests_ambiguous,
                        "requests_deferred": last_model_cycle.requests_deferred,
                    }
                ),
            }
        )

    def _readiness_envelope(self, observed_at: datetime) -> SignedEnvelopeV1:
        now = observed_at.astimezone(timezone.utc)
        valid_until = now + timedelta(
            seconds=self.config.readiness_ttl_seconds
        )
        health_status, reasons = self._health()
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
                "status": health_status,
                "reason_codes": reasons,
                "runner_config_sha256": self.config.runner_config_sha256,
                "core_public_key_sha256": self.config.core_public_key_sha256,
                "model_gateway_public_key_sha256": (
                    self.config.model_gateway_public_key_sha256
                ),
                "model_gateway_runner_config_sha256": (
                    self.config.model_gateway_runner_config_sha256
                ),
                "allocator_key_id": self.allocator_signer.key_id,
                "candidate_only": True,
                "evaluation_authority": False,
                "promotion_authority": False,
                "capital_grant_authority": False,
            },
            capabilities=(
                "allocator-request-client",
                "budgeted-model-spool-relay",
                "candidate-only-intake",
                "credential-separated-model-worker",
                "no-provider-retry-after-dispatch",
                "point-in-time-validation",
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
                    "research-mesh-readiness",
                    self.instance_id,
                    self._readiness_sequence,
                    now.isoformat(),
                ]
            ),
            not_before=now,
            expires_at=valid_until,
            signer=self.readiness_signer,
        )

    def write_readiness(
        self,
        observed_at: datetime | None = None,
    ) -> SignedEnvelopeV1:
        envelope = self._readiness_envelope(
            observed_at or datetime.now(timezone.utc)
        )
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
        if self.server is None:
            raise RuntimeError("Research Mesh service is not bound")
        self.server.timeout = 0.25
        processing_interval = (
            self.config.processing_interval_milliseconds / 1000
        )
        readiness_interval = max(
            1.0,
            self.config.readiness_ttl_seconds / 2,
        )
        next_processing = 0.0
        next_readiness = 0.0
        while not stop_event.is_set():
            monotonic = time.monotonic()
            if monotonic >= next_processing:
                try:
                    model_cycle = self.model_relay.process_cycle()
                except Exception:
                    with self._status_lock:
                        self._last_model_cycle_failed = True
                else:
                    with self._status_lock:
                        self._last_model_cycle = model_cycle
                        self._last_model_cycle_failed = False
                try:
                    cycle = self.mesh.process_cycle()
                except Exception:
                    with self._status_lock:
                        self._last_cycle_failed = True
                else:
                    with self._status_lock:
                        self._last_cycle = cycle
                        self._last_cycle_failed = False
                next_processing = monotonic + processing_interval
            if monotonic >= next_readiness:
                self.write_readiness()
                next_readiness = monotonic + readiness_interval
            self.server.handle_request()

    def close(self) -> None:
        if self.server is not None:
            self.server.server_close()
            self.server = None
        try:
            self.model_relay.close()
        finally:
            try:
                self.state.close()
            finally:
                self.data_root_lease.close()

    def __enter__(self) -> "ResearchMeshService":
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        self.close()


def build_research_mesh_service(
    config: ResearchMeshRunnerConfig,
    credential_provider: CredentialProvider,
    *,
    transport: CoreTransport | None = None,
    model_gateway_transport: ModelGatewayTransport | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ResearchMeshService:
    readiness_public_key = _read_public_key(
        config.research_mesh_public_key_path,
        config.research_mesh_public_key_sha256,
        "Research Mesh public key",
    )
    allocator_public_key = _read_public_key(
        config.allocator_public_key_path,
        config.allocator_public_key_sha256,
        "allocator public key",
    )
    core_public_key = _read_public_key(
        config.core_public_key_path,
        config.core_public_key_sha256,
        "Core public key",
    )
    model_gateway_public_key = _read_public_key(
        config.model_gateway_public_key_path,
        config.model_gateway_public_key_sha256,
        "Model Gateway public key",
    )
    _verified_file(
        config.fund_lock_path,
        config.fund_lock_sha256,
        "fund lock",
    )
    _verified_file(
        config.service_manifest_path,
        config.service_manifest_sha256,
        "service manifest",
    )
    readiness_signer = Ed25519Signer.from_private_bytes(
        _read_seed(
            credential_provider,
            config.credential_targets["research_mesh_signing_seed"],
            "Research Mesh signing credential",
        )
    )
    allocator_signer = Ed25519Signer.from_private_bytes(
        _read_seed(
            credential_provider,
            config.credential_targets["allocator_signing_seed"],
            "allocator signing credential",
        )
    )
    if readiness_signer.public_key_bytes != readiness_public_key:
        raise CredentialProviderError(
            "Research Mesh signing credential does not match its pinned public key"
        )
    if allocator_signer.public_key_bytes != allocator_public_key:
        raise CredentialProviderError(
            "allocator signing credential does not match its pinned public key"
        )
    if len(
        {
            readiness_signer.key_id,
            allocator_signer.key_id,
            hashlib.sha256(core_public_key).hexdigest(),
            hashlib.sha256(model_gateway_public_key).hexdigest(),
        }
    ) != 4:
        raise CredentialProviderError(
            "Research Mesh, allocator, Core, and Model Gateway keys must be distinct"
        )
    allocator_token = _read_token(
        credential_provider,
        config.credential_targets["allocator_bearer_token"],
        "allocator bearer credential",
    )
    model_gateway_token = _read_token(
        credential_provider,
        config.credential_targets["model_gateway_client_token"],
        "Model Gateway client credential",
    )
    identity = CoreIdentity(
        readiness_path=config.core_readiness_path,
        public_key=core_public_key,
        release_id=config.release_id,
        fund_lock_sha256=config.fund_lock_sha256,
        service_manifest_sha256=config.service_manifest_sha256,
        host="127.0.0.1",
    )
    core_client = AllocatorCoreClient(
        identity=identity,
        readiness_verifier=CoreReadinessVerifier(identity),
        transport=transport or UrllibCoreTransport(),
        bearer_token=allocator_token,
        timeout_seconds=config.core_timeout_seconds,
    )
    model_gateway_identity = ModelGatewayIdentity(
        readiness_path=config.model_gateway_readiness_path,
        public_key=model_gateway_public_key,
        public_key_file_sha256=config.model_gateway_public_key_sha256,
        runner_config_sha256=config.model_gateway_runner_config_sha256,
        release_id=config.release_id,
        fund_lock_sha256=config.fund_lock_sha256,
        service_manifest_sha256=config.service_manifest_sha256,
    )
    model_gateway_readiness = ModelGatewayReadinessVerifier(
        model_gateway_identity
    )
    model_gateway_client = ModelGatewayClient(
        identity=model_gateway_identity,
        readiness_verifier=model_gateway_readiness,
        transport=model_gateway_transport or UrllibModelGatewayTransport(),
        bearer_token=model_gateway_token,
        timeout_seconds=config.model_gateway_timeout_seconds,
        max_response_bytes=config.model_response_max_bytes,
        max_prompt_characters=config.model_prompt_max_characters,
        clock=clock,
    )
    data_root_lease = DataRootLease.acquire(config.data_root)
    try:
        state = ResearchMeshState(config.data_root / "research-mesh.db")
        model_relay: ModelSpoolRelay | None = None
        try:
            model_state = ModelSpoolState(config.data_root / "model-spool.db")
            try:
                model_relay = ModelSpoolRelay(
                    request_inbox=config.model_request_inbox,
                    response_outbox=config.model_response_outbox,
                    outcome_outbox=config.model_outcome_outbox,
                    state=model_state,
                    client=model_gateway_client,
                    readiness_verifier=model_gateway_readiness,
                    clock=clock,
                    max_request_bytes=config.model_request_max_bytes,
                    max_response_bytes=config.model_response_max_bytes,
                    max_prompt_characters=config.model_prompt_max_characters,
                    max_output_tokens=config.model_max_output_tokens,
                    max_reserve_microusd=config.model_max_reserve_microusd,
                )
            except BaseException:
                model_state.close()
                raise
            assert model_relay is not None
            mesh = ResearchMesh(
                candidate_inbox=config.candidate_inbox,
                allocation_inbox=config.allocation_inbox,
                state=state,
                allocator_signer=allocator_signer,
                core_client=core_client,
                clock=clock,
                max_input_bytes=config.max_input_bytes,
            )
            service = ResearchMeshService(
                config=config,
                mesh=mesh,
                model_relay=model_relay,
                state=state,
                readiness_signer=readiness_signer,
                allocator_signer=allocator_signer,
                data_root_lease=data_root_lease,
                instance_id=str(uuid.uuid4()),
            )
            service.bind()
            return service
        except BaseException:
            if model_relay is not None:
                model_relay.close()
            state.close()
            raise
    except BaseException:
        data_root_lease.close()
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m blunder.fund.research_entrypoint",
        description="Run the private loopback-only DumbMoney Research Mesh.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Absolute public Research Mesh config path.",
    )
    parser.add_argument(
        "--config-sha256",
        required=True,
        help="Pinned SHA-256 of the exact public config bytes.",
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
        config = ResearchMeshRunnerConfig.load(
            args.config,
            expected_file_sha256=args.config_sha256,
        )
        with build_research_mesh_service(
            config,
            WindowsCredentialManager(),
        ) as service:
            readiness = service.write_readiness()
            host, port = service.endpoint
            print(
                json.dumps(
                    {
                        "schema": "dumbmoney.research-mesh-ready.v1",
                        "service_name": READINESS_SOURCE,
                        "host": host,
                        "port": port,
                        "process_id": os.getpid(),
                        "instance_id": service.instance_id,
                        "readiness_path": str(config.readiness_path),
                        "signer_key_id": readiness.signer_key_id,
                        "authority": "CANDIDATE_ONLY_NO_CAPITAL_GRANT",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                flush=True,
            )
            service.serve(stop_event)
    except (CredentialProviderError, OSError, RuntimeError, TypeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema": "dumbmoney.research-mesh-startup-error.v1",
                    "code": type(exc).__name__,
                    "message": str(exc),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
