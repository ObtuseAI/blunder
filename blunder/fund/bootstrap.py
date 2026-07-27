"""Safe construction and loopback HTTP hooks for a DumbMoney sidecar.

This module deliberately has no CLI that can invent keys or authority.  A
runner must inject an OS-backed core signer, explicit public-key role sets,
and the SHA-256 hash of a high-entropy operator bearer token.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit

from blunder.fund.canonical import require_digest
from blunder.fund.cas import ContentAddressedStore
from blunder.fund.contracts import Venue
from blunder.fund.crypto import Ed25519Keyring, EnvelopeSigner
from blunder.fund.ledger import EventLedger
from blunder.fund.policy import RiskPolicyEngine, RiskPolicyV1
from blunder.fund.runtime import DeploymentAuthorityBindings, FundControlPlane
from blunder.fund.service import (
    ALLOCATOR_EVENT_APPEND_PATH,
    CONTROL_SNAPSHOT_PATH,
    HEALTH_LIVE_PATH,
    ApiResponse,
    FundApi,
)


@dataclass(frozen=True)
class RolePublicKeys:
    operator: tuple[bytes, ...]
    evaluator: tuple[bytes, ...] = ()
    promoter: tuple[bytes, ...] = ()
    allocator: tuple[bytes, ...] = ()
    research: tuple[bytes, ...] = ()
    dummy_venue: tuple[bytes, ...] = ()
    dopey_venue: tuple[bytes, ...] = ()
    migration: tuple[bytes, ...] = ()


@dataclass
class FundApplication:
    """Owned runtime resources returned to a supervised sidecar runner."""

    api: FundApi
    control_plane: FundControlPlane
    ledger: EventLedger
    cas: ContentAddressedStore

    def close(self) -> None:
        self.ledger.close()

    def __enter__(self) -> "FundApplication":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


def build_fund_application(
    *,
    data_root: Path,
    policy_path: Path,
    core_signer: EnvelopeSigner,
    role_public_keys: RolePublicKeys,
    verified_policy: RiskPolicyV1 | None = None,
    deployment_bindings: DeploymentAuthorityBindings | None = None,
    clock: Callable[[], datetime] | None = None,
    model_status_provider: Callable[[datetime], Mapping[str, object]] | None = None,
) -> FundApplication:
    """Construct the control plane from injected keys and explicit paths."""

    root = data_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    keyring = Ed25519Keyring()
    # EnvelopeSigner intentionally does not require public bytes.  The supplied
    # signer must therefore also be registered by a role-independent runner
    # adapter exposing its public bytes.
    raw_core_public_key = getattr(core_signer, "public_key_bytes", None)
    if not isinstance(raw_core_public_key, bytes):
        raise TypeError("core_signer must expose raw 32-byte public_key_bytes")
    registered_core_id = keyring.register(raw_core_public_key)
    if registered_core_id != core_signer.key_id:
        raise ValueError("core signer key ID does not match its public key")
    role_ids: dict[str, frozenset[str]] = {}
    for role, public_keys in (
        ("operator", role_public_keys.operator),
        ("evaluator", role_public_keys.evaluator),
        ("promoter", role_public_keys.promoter),
        ("allocator", role_public_keys.allocator),
        ("research", role_public_keys.research),
        ("migration", role_public_keys.migration),
    ):
        role_ids[role] = frozenset(keyring.register(public_key) for public_key in public_keys)
    policy = (
        RiskPolicyV1.load(policy_path)
        if verified_policy is None
        else verified_policy
    )
    cas = ContentAddressedStore(root / "objects")
    ledger = EventLedger(root / "control.db", cas, keyring, clock=clock)
    try:
        control_plane = FundControlPlane(
            ledger,
            keyring,
            core_signer,
            RiskPolicyEngine(policy),
            operator_key_ids=role_ids["operator"],
            evaluator_key_ids=role_ids["evaluator"],
            promoter_key_ids=role_ids["promoter"],
            allocator_key_ids=role_ids["allocator"],
            research_key_ids=role_ids["research"],
            venue_key_ids={
                Venue.DUMMY_KALSHI: frozenset(
                    keyring.register(public_key) for public_key in role_public_keys.dummy_venue
                ),
                Venue.DOPEY_ROBINHOOD: frozenset(
                    keyring.register(public_key) for public_key in role_public_keys.dopey_venue
                ),
            },
            migration_key_ids=role_ids["migration"],
            deployment_bindings=deployment_bindings,
            clock=clock,
            model_status_provider=model_status_provider,
        )
    except BaseException:
        ledger.close()
        raise
    return FundApplication(FundApi(control_plane), control_plane, ledger, cas)


def make_loopback_server(
    application: FundApplication,
    *,
    port: int,
    operator_token_sha256: str,
    desktop_read_token_sha256: str | None = None,
    allocator_token_sha256: str | None = None,
    cell_token_sha256: Mapping[str, str] | None = None,
    max_request_bytes: int = 1_048_576,
) -> ThreadingHTTPServer:
    """Build, but do not start, an authenticated loopback-only HTTP server."""

    require_digest(operator_token_sha256, "operator_token_sha256")
    if desktop_read_token_sha256 is not None:
        require_digest(
            desktop_read_token_sha256,
            "desktop_read_token_sha256",
        )
    if allocator_token_sha256 is not None:
        require_digest(allocator_token_sha256, "allocator_token_sha256")
    cell_tokens = dict(cell_token_sha256 or {})
    unknown_cells = set(cell_tokens) - {venue.value for venue in Venue}
    if unknown_cells:
        raise ValueError(f"cell_token_sha256 contains unsupported cells: {sorted(unknown_cells)}")
    for cell_id, digest in cell_tokens.items():
        require_digest(digest, f"cell_token_sha256.{cell_id}")
    all_token_digests = [
        operator_token_sha256,
        *(
            []
            if desktop_read_token_sha256 is None
            else [desktop_read_token_sha256]
        ),
        *([] if allocator_token_sha256 is None else [allocator_token_sha256]),
        *cell_tokens.values(),
    ]
    if len(set(all_token_digests)) != len(all_token_digests):
        raise ValueError(
            "desktop-read, operator, allocator, and per-cell tokens must be distinct"
        )
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65_535:
        raise ValueError("port must be an integer from 0 through 65535")
    if isinstance(max_request_bytes, bool) or not isinstance(max_request_bytes, int) or max_request_bytes <= 0:
        raise ValueError("max_request_bytes must be a positive integer")

    class Handler(BaseHTTPRequestHandler):
        server_version = "DumbMoneyCore/1"

        def log_message(self, _format: str, *_args: object) -> None:
            # The supervising runner owns structured, redacted request logging.
            return

        @staticmethod
        def _raw_token_matches(token: str, expected_digest: str) -> bool:
            if not token or any(character.isspace() for character in token):
                return False
            observed = hashlib.sha256(token.encode("utf-8")).hexdigest()
            return hmac.compare_digest(observed, expected_digest)

        def _bearer_matches(self, expected_digest: str) -> bool:
            header = self.headers.get("Authorization", "")
            prefix = "Bearer "
            if not header.startswith(prefix):
                return False
            return self._raw_token_matches(header[len(prefix) :], expected_digest)

        def _authorized(self) -> bool:
            route_path = urlsplit(self.path).path
            if route_path == HEALTH_LIVE_PATH:
                return True
            parts = route_path.split("/")
            if (
                len(parts) == 5
                and parts[1:3] == ["v1", "cells"]
                and parts[4] == "commands"
            ):
                expected = cell_tokens.get(parts[3])
                return expected is not None and self._bearer_matches(expected)
            if (
                len(parts) == 6
                and parts[1:3] == ["v1", "cells"]
                and parts[4] == "contracts"
            ):
                expected = cell_tokens.get(parts[3])
                return expected is not None and self._bearer_matches(expected)
            if (
                len(parts) == 5
                and parts[1:3] == ["v1", "cells"]
                and parts[4] == "journal-heads:anchor"
            ):
                expected = cell_tokens.get(parts[3])
                return (
                    expected is not None
                    and self._bearer_matches(expected)
                )
            if route_path == ALLOCATOR_EVENT_APPEND_PATH:
                return (
                    allocator_token_sha256 is not None
                    and self._bearer_matches(allocator_token_sha256)
                )
            if route_path == CONTROL_SNAPSHOT_PATH:
                desktop_token = self.headers.get("X-Blunder-Token", "")
                return self._bearer_matches(operator_token_sha256) or (
                    desktop_read_token_sha256 is not None
                    and self._raw_token_matches(
                        desktop_token,
                        desktop_read_token_sha256,
                    )
                )
            return self._bearer_matches(operator_token_sha256)

        @staticmethod
        def _unauthorized() -> ApiResponse:
            body = json.dumps(
                {"schema": "dumbmoney.api-error.v1", "code": "UNAUTHORIZED"},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            return ApiResponse(
                401,
                {
                    "content-type": "application/json; charset=utf-8",
                    "cache-control": "no-store",
                    "www-authenticate": "Bearer",
                },
                body,
            )

        def _dispatch(self) -> None:
            if not self._authorized():
                self._write(self._unauthorized())
                return
            raw_length = self.headers.get("Content-Length")
            body: bytes | None = None
            if raw_length is not None:
                try:
                    length = int(raw_length)
                except ValueError:
                    self.send_error(400, "invalid Content-Length")
                    return
                if length < 0 or length > max_request_bytes:
                    self.send_error(413, "request body exceeds configured limit")
                    return
                body = self.rfile.read(length)
            self._write(application.api.handle(self.command, self.path, body))

        def _write(self, response: ApiResponse) -> None:
            self.send_response(response.status)
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.send_header("content-length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

        do_GET = _dispatch
        do_POST = _dispatch

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server
