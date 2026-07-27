"""Framework-neutral event API primitives for the DumbMoney control plane."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, cast
from urllib.parse import parse_qs, urlsplit

from blunder.fund.canonical import loads_strict_json
from blunder.fund.contracts import SignedEnvelopeV1
from blunder.fund.ledger import LedgerConflictError, LedgerIntegrityError
from blunder.fund.runtime import AuthorityError, EvidenceResolutionError, FundControlPlane
from blunder.fund.policy import PolicyViolation
from blunder.fund.crypto import SignatureVerificationError


CONTROL_SNAPSHOT_PATH = "/api/v1/fund/control-snapshot"
CONTROL_SNAPSHOT_ALIAS = "/v1/control-snapshot"
EVENT_APPEND_PATH = "/v1/events:append"
ALLOCATOR_EVENT_APPEND_PATH = "/v1/allocator/events:append"
DESIRED_MODE_PATH = "/v1/operator/desired-mode"
HEALTH_LIVE_PATH = "/health/live"
HEALTH_READY_PATH = "/health/ready"
METRICS_PATH = "/metrics"
CELL_COMMAND_PATH_TEMPLATE = "/v1/cells/{cell_id}/commands"
CELL_CONTRACT_PATH_TEMPLATE = "/v1/cells/{cell_id}/contracts/{body_digest}"
CELL_JOURNAL_ANCHOR_PATH_TEMPLATE = (
    "/v1/cells/{cell_id}/journal-heads:anchor"
)


@dataclass(frozen=True)
class ApiResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> Mapping[str, object]:
        value = json.loads(self.body)
        if not isinstance(value, dict):
            raise TypeError("API response must be an object")
        return value


class FundApi:
    """Small routing surface embeddable in the existing Blunder HTTP server."""

    def __init__(self, control_plane: FundControlPlane) -> None:
        self.control_plane = control_plane

    @staticmethod
    def _json_response(status: int, body: Mapping[str, object]) -> ApiResponse:
        return ApiResponse(
            status=status,
            headers={
                "content-type": "application/json; charset=utf-8",
                "cache-control": "no-store",
                "x-content-type-options": "nosniff",
            },
            body=json.dumps(
                body,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8"),
        )

    def handle(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        *,
        observed_at: datetime | None = None,
    ) -> ApiResponse:
        try:
            parsed_url = urlsplit(path)
            route_path = parsed_url.path
            if method == "GET" and route_path in {CONTROL_SNAPSHOT_PATH, CONTROL_SNAPSHOT_ALIAS}:
                return self._json_response(
                    200,
                    self.control_plane.control_snapshot(observed_at=observed_at),
                )
            if method == "GET" and route_path == HEALTH_LIVE_PATH:
                return self._json_response(200, {"schema": "dumbmoney.health.v1", "status": "LIVE"})
            if method == "GET" and route_path == HEALTH_READY_PATH:
                snapshot = self.control_plane.control_snapshot(observed_at=observed_at)
                ready = snapshot["status"] == "LIVE_READY"
                return self._json_response(
                    200 if ready else 503,
                    {
                        "schema": "dumbmoney.health.v1",
                        "status": "READY" if ready else "NOT_READY",
                        "reason_codes": snapshot["reason_codes"],
                    },
                )
            if method == "GET" and route_path == METRICS_PATH:
                sequence, _head = self.control_plane.ledger.head()
                return ApiResponse(
                    200,
                    {
                        "content-type": "text/plain; version=0.0.4; charset=utf-8",
                        "cache-control": "no-store",
                    },
                    f"dumbmoney_ledger_events_total {sequence}\n".encode("ascii"),
                )
            path_parts = route_path.split("/")
            if (
                method == "GET"
                and len(path_parts) == 5
                and path_parts[1:3] == ["v1", "cells"]
                and path_parts[4] == "commands"
            ):
                query = parse_qs(parsed_url.query, keep_blank_values=True)
                expected_keys = {"after", "cursor", "request_nonce"}
                if not expected_keys <= set(query) or any(len(query[key]) != 1 for key in expected_keys):
                    raise ValueError(
                        "cell command request requires exactly one after, cursor, and request_nonce value"
                    )
                unknown = set(query) - {"after", "cursor", "request_nonce", "limit"}
                if unknown:
                    raise ValueError(f"cell command request contains unknown query keys: {sorted(unknown)}")
                if "limit" in query and len(query["limit"]) != 1:
                    raise ValueError("cell command request accepts at most one limit value")
                try:
                    after = int(query["after"][0])
                    limit = int(query.get("limit", ["250"])[0])
                except ValueError as exc:
                    raise ValueError("cell command after and limit values must be integers") from exc
                page = self.control_plane.cell_commands(
                    cell_id=path_parts[3],
                    after_sequence=after,
                    after_digest=query["cursor"][0],
                    request_nonce=query["request_nonce"][0],
                    limit=limit,
                    observed_at=observed_at,
                )
                return self._json_response(200, page)
            if (
                method == "GET"
                and len(path_parts) == 6
                and path_parts[1:3] == ["v1", "cells"]
                and path_parts[4] == "contracts"
            ):
                query = parse_qs(parsed_url.query, keep_blank_values=True)
                expected_keys = {"capital_envelope_digest", "request_nonce"}
                if set(query) != expected_keys or any(
                    len(query[key]) != 1 for key in expected_keys
                ):
                    raise ValueError(
                        "cell contract resolution requires exactly one capital_envelope_digest "
                        "and request_nonce value"
                    )
                resolution = self.control_plane.cell_contract_resolution(
                    cell_id=path_parts[3],
                    body_digest=path_parts[5],
                    capital_envelope_digest=query["capital_envelope_digest"][0],
                    request_nonce=query["request_nonce"][0],
                    observed_at=observed_at,
                )
                return self._json_response(200, resolution)
            if (
                method == "POST"
                and len(path_parts) == 5
                and path_parts[1:3] == ["v1", "cells"]
                and path_parts[4] == "journal-heads:anchor"
            ):
                if parsed_url.query:
                    raise ValueError(
                        "cell journal anchor endpoint accepts no query"
                    )
                if body is None:
                    raise ValueError(
                        "cell journal anchor request body is required"
                    )
                request = loads_strict_json(
                    body,
                    "cell journal anchor request",
                )
                expected_fields = {
                    "schema",
                    "cell_id",
                    "account_hash",
                    "journal_name",
                    "journal_schema",
                    "journal_stream_id",
                    "journal_sequence",
                    "journal_head_sha256",
                    "request_nonce",
                }
                if set(request) != expected_fields:
                    raise ValueError(
                        "cell journal anchor request fields mismatch"
                    )
                if (
                    request.get("schema")
                    != "dumbmoney.cell-journal-anchor-request.v1"
                    or request.get("cell_id") != path_parts[3]
                ):
                    raise ValueError(
                        "cell journal anchor request identity mismatch"
                    )
                anchored = self.control_plane.anchor_cell_journal_head(
                    cell_id=path_parts[3],
                    account_hash=cast(str, request["account_hash"]),
                    journal_name=cast(str, request["journal_name"]),
                    journal_schema=cast(str, request["journal_schema"]),
                    journal_stream_id=cast(
                        str,
                        request["journal_stream_id"],
                    ),
                    journal_sequence=cast(
                        int,
                        request["journal_sequence"],
                    ),
                    journal_head_sha256=cast(
                        str,
                        request["journal_head_sha256"],
                    ),
                    request_nonce=cast(str, request["request_nonce"]),
                    observed_at=observed_at,
                )
                return self._json_response(200, anchored)
            if method == "POST" and route_path in {
                EVENT_APPEND_PATH,
                ALLOCATOR_EVENT_APPEND_PATH,
                DESIRED_MODE_PATH,
            }:
                if body is None:
                    raise ValueError("request body is required")
                raw = loads_strict_json(body, "signed event request")
                envelope = SignedEnvelopeV1.from_dict(raw)
                if route_path == DESIRED_MODE_PATH and envelope.body_schema != "dumbmoney.desired-mode.v1":
                    raise ValueError("desired-mode endpoint accepts DesiredModeV1 only")
                if (
                    route_path == ALLOCATOR_EVENT_APPEND_PATH
                    and envelope.body_schema != "dumbmoney.capital-request.v1"
                ):
                    raise ValueError(
                        "allocator event endpoint accepts CapitalRequestV1 only"
                    )
                record = self.control_plane.accept_envelope(envelope)
                return self._json_response(
                    200 if record.duplicate else 201,
                    {
                        "schema": "dumbmoney.event-append-result.v1",
                        "event_id": record.event_id,
                        "global_sequence": record.global_sequence,
                        "event_digest": record.event_digest,
                        "duplicate": record.duplicate,
                    },
                )
            return self._json_response(
                404,
                {"schema": "dumbmoney.api-error.v1", "code": "NOT_FOUND"},
            )
        except SignatureVerificationError as exc:
            return self._error(401, "INVALID_SIGNATURE", exc)
        except (AuthorityError, EvidenceResolutionError, PolicyViolation, PermissionError) as exc:
            return self._error(403, "AUTHORITY_DENIED", exc)
        except (LedgerConflictError, LedgerIntegrityError) as exc:
            return self._error(409, "LEDGER_CONFLICT", exc)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._error(400, "INVALID_REQUEST", exc)

    def _error(self, status: int, code: str, exc: Exception) -> ApiResponse:
        # Messages contain validation context only; contracts forbid secrets.
        return self._json_response(
            status,
            {
                "schema": "dumbmoney.api-error.v1",
                "code": code,
                "message": str(exc),
            },
        )
