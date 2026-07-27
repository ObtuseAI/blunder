from __future__ import annotations

import base64
import binascii
import hashlib
import importlib
import ipaddress
import uuid
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from .common import (
    HEX_64_RE,
    ValidationReport,
    canonical_json_bytes,
    load_json,
    parse_utc,
    sha256_bytes,
)


EXPECTED_SERVICES = frozenset(
    {
        "DumbMoneyCore",
        "DumbMoneyResearchMesh",
        "DumbMoneyModelGateway",
        "DumbMoneyDummyKalshi",
        "DumbMoneyDopeyRobinhood",
    }
)

SignatureVerifier = Callable[[bytes, bytes, str, str], bool]


def load_readiness_schema(schema_path: Path | None = None) -> dict[str, Any]:
    path = schema_path or (
        Path(__file__).resolve().parent
        / "schemas"
        / "readiness-descriptor.v1.schema.json"
    )
    return load_json(path)


def make_ed25519_verifier(
    public_key_path: Path, *, expected_key_id: str | None = None
) -> SignatureVerifier:
    """Build an Ed25519 verifier when the bounded crypto package exists."""

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except ImportError as exc:
        raise RuntimeError(
            "cryptography is required for public-key readiness verification"
        ) from exc

    raw = public_key_path.read_bytes()
    try:
        loaded = serialization.load_pem_public_key(raw)
    except ValueError:
        if len(raw) != 32:
            raise ValueError(
                "Ed25519 public key must be PEM or exactly 32 raw bytes"
            ) from None
        loaded = Ed25519PublicKey.from_public_bytes(raw)
    if not isinstance(loaded, Ed25519PublicKey):
        raise ValueError("public key is not Ed25519")
    public_bytes = loaded.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    actual_key_id = hashlib.sha256(public_bytes).hexdigest()
    if expected_key_id is not None and expected_key_id != actual_key_id:
        raise ValueError("expected key ID does not match the supplied public key")

    def verify(payload: bytes, signature: bytes, key_id: str, algorithm: str) -> bool:
        if algorithm != "Ed25519":
            return False
        if key_id != actual_key_id:
            return False
        try:
            loaded.verify(signature, payload)
        except Exception:
            return False
        return True

    return verify


def _decode_signature(report: ValidationReport, encoded: Any) -> bytes | None:
    if not isinstance(encoded, str) or not encoded or "=" in encoded:
        report.error(
            "READINESS_SIGNATURE_INVALID_ENCODING",
            "signature must be canonical unpadded URL-safe base64",
            "signature",
        )
        return None
    try:
        decoded = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error):
        report.error(
            "READINESS_SIGNATURE_INVALID_ENCODING",
            "signature must be canonical unpadded URL-safe base64",
            "signature",
        )
        return None
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != encoded:
        report.error(
            "READINESS_SIGNATURE_NONCANONICAL",
            "signature must use canonical unpadded URL-safe base64",
            "signature",
        )
        return None
    if len(decoded) != 64:
        report.error(
            "READINESS_SIGNATURE_INVALID_LENGTH",
            "an Ed25519 signature must contain exactly 64 decoded bytes",
            "signature",
        )
        return None
    return decoded


def _validate_endpoint(report: ValidationReport, endpoint: Any) -> None:
    if not isinstance(endpoint, dict):
        report.error(
            "READINESS_ENDPOINT_MISSING",
            "body.endpoint must be an object",
            "body.endpoint",
        )
        return
    extra_fields = sorted(set(endpoint) - {"transport", "host", "port", "base_path"})
    if extra_fields:
        report.error(
            "READINESS_ENDPOINT_FIELDS_FORBIDDEN",
            f"unexpected readiness endpoint fields: {extra_fields}",
            "body.endpoint",
        )
    if endpoint.get("transport") != "http":
        report.error(
            "READINESS_TRANSPORT_FORBIDDEN",
            "DumbMoney readiness endpoints must use loopback HTTP",
            "body.endpoint.transport",
        )
    host = endpoint.get("host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        report.error(
            "READINESS_HOST_INVALID",
            "readiness endpoint host must be a literal loopback address",
            "body.endpoint.host",
        )
    else:
        if not address.is_loopback:
            report.error(
                "READINESS_HOST_NOT_LOOPBACK",
                "readiness endpoints may not bind to a non-loopback address",
                "body.endpoint.host",
            )
    port = endpoint.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        report.error(
            "READINESS_PORT_INVALID",
            "dynamic readiness port must be an integer from 1024 through 65535",
            "body.endpoint.port",
        )
    if endpoint.get("base_path") != "/":
        report.error(
            "READINESS_BASE_PATH_INVALID",
            "readiness descriptor base_path must be /",
            "body.endpoint.base_path",
        )


def _validate_parallel_signed_envelope(
    report: ValidationReport, descriptor: dict[str, Any]
) -> None:
    try:
        module = importlib.import_module("blunder.fund.contracts")
        envelope_type = getattr(module, "SignedEnvelopeV1")
    except (ImportError, ModuleNotFoundError, AttributeError):
        report.partial(
            "PARALLEL_FUND_CONTRACT_UNAVAILABLE",
            "blunder.fund SignedEnvelopeV1 is not available yet",
            "blunder/fund",
        )
        return
    try:
        envelope_type.from_dict(descriptor)
    except Exception as exc:
        report.error(
            "PARALLEL_FUND_CONTRACT_REJECTED",
            f"SignedEnvelopeV1 rejected readiness data: {type(exc).__name__}: {exc}",
            "blunder/fund",
        )


def validate_readiness_descriptor(
    descriptor: dict[str, Any],
    *,
    expected_service: str | None = None,
    verifier: SignatureVerifier | None = None,
    require_parallel_contract: bool = False,
    now_utc: Any | None = None,
) -> ValidationReport:
    """Validate a dynamic readiness body carried by SignedEnvelopeV1."""

    report = ValidationReport("readiness-descriptor")
    allowed_envelope_fields = {
        "schema",
        "source_id",
        "source_sequence",
        "event_id",
        "correlation_id",
        "causation_id",
        "nonce",
        "not_before",
        "expires_at",
        "body_schema",
        "body_digest",
        "body",
        "signature_algorithm",
        "signer_key_id",
        "signature",
    }
    extra_envelope_fields = sorted(set(descriptor) - allowed_envelope_fields)
    if extra_envelope_fields:
        report.error(
            "READINESS_ENVELOPE_FIELDS_FORBIDDEN",
            f"unexpected signed envelope fields: {extra_envelope_fields}",
            "envelope",
        )
    if descriptor.get("schema") != "dumbmoney.signed-envelope.v1":
        report.error(
            "READINESS_ENVELOPE_SCHEMA_UNSUPPORTED",
            "schema must be dumbmoney.signed-envelope.v1",
            "schema",
        )
    if descriptor.get("body_schema") != "dumbmoney.readiness-descriptor.v1":
        report.error(
            "READINESS_BODY_SCHEMA_UNSUPPORTED",
            "body_schema must be dumbmoney.readiness-descriptor.v1",
            "body_schema",
        )

    body = descriptor.get("body")
    if not isinstance(body, dict):
        report.error(
            "READINESS_BODY_MISSING",
            "body must be an object",
            "body",
        )
        return report
    if body.get("schema") != "dumbmoney.readiness-descriptor.v1":
        report.error(
            "READINESS_EMBEDDED_SCHEMA_MISMATCH",
            "body.schema must match body_schema",
            "body.schema",
        )
    allowed_body_fields = {
        "schema",
        "service_name",
        "release_id",
        "instance_id",
        "process_id",
        "generation",
        "observed_at",
        "valid_until",
        "endpoint",
        "fund_lock_sha256",
        "service_manifest_sha256",
        "authority",
        "health",
        "capabilities",
    }
    extra_body_fields = sorted(set(body) - allowed_body_fields)
    if extra_body_fields:
        report.error(
            "READINESS_BODY_FIELDS_FORBIDDEN",
            f"unexpected readiness fields: {extra_body_fields}",
            "body",
        )

    body_digest = descriptor.get("body_digest")
    try:
        observed_body_digest = sha256_bytes(canonical_json_bytes(body))
    except (TypeError, ValueError) as exc:
        observed_body_digest = None
        report.error(
            "READINESS_BODY_NOT_CANONICAL",
            str(exc),
            "body",
        )
    if not isinstance(body_digest, str) or HEX_64_RE.fullmatch(body_digest) is None:
        report.error(
            "READINESS_BODY_DIGEST_INVALID",
            "body_digest must be a SHA-256 digest",
            "body_digest",
        )
    elif observed_body_digest is not None and body_digest != observed_body_digest:
        report.error(
            "READINESS_BODY_DIGEST_MISMATCH",
            "body does not match body_digest",
            "body_digest",
        )

    event_id = descriptor.get("event_id")
    event_identity = dict(descriptor)
    event_identity.pop("event_id", None)
    event_identity.pop("signature", None)
    try:
        observed_event_id = sha256_bytes(canonical_json_bytes(event_identity))
    except (TypeError, ValueError) as exc:
        observed_event_id = None
        report.error(
            "READINESS_ENVELOPE_NOT_CANONICAL",
            str(exc),
            "envelope",
        )
    if not isinstance(event_id, str) or HEX_64_RE.fullmatch(event_id) is None:
        report.error(
            "READINESS_EVENT_ID_INVALID",
            "event_id must be a SHA-256 digest",
            "event_id",
        )
    elif observed_event_id is not None and event_id != observed_event_id:
        report.error(
            "READINESS_EVENT_ID_MISMATCH",
            "envelope identity does not match event_id",
            "event_id",
        )

    source_sequence = descriptor.get("source_sequence")
    if (
        not isinstance(source_sequence, int)
        or isinstance(source_sequence, bool)
        or source_sequence <= 0
    ):
        report.error(
            "READINESS_SOURCE_SEQUENCE_INVALID",
            "source_sequence must be a positive integer",
            "source_sequence",
        )
    for identifier in ("source_id", "correlation_id", "nonce"):
        value = descriptor.get(identifier)
        if not isinstance(value, str) or not value:
            report.error(
                "READINESS_ENVELOPE_IDENTIFIER_INVALID",
                f"{identifier} must be a non-empty string",
                identifier,
            )
    causation_id = descriptor.get("causation_id")
    if causation_id is not None and (
        not isinstance(causation_id, str) or HEX_64_RE.fullmatch(causation_id) is None
    ):
        report.error(
            "READINESS_CAUSATION_ID_INVALID",
            "causation_id must be null or a SHA-256 digest",
            "causation_id",
        )

    service_name = body.get("service_name")
    if service_name not in EXPECTED_SERVICES:
        report.error(
            "READINESS_SERVICE_UNKNOWN",
            "service_name is not one of the five DumbMoney services",
            "body.service_name",
        )
    if descriptor.get("source_id") != service_name:
        report.error(
            "READINESS_SOURCE_SERVICE_MISMATCH",
            "signed source_id must equal body.service_name",
            "source_id",
        )
    if expected_service is not None and service_name != expected_service:
        report.error(
            "READINESS_SERVICE_MISMATCH",
            f"expected readiness for {expected_service}, received {service_name}",
            "body.service_name",
        )
    release_id = body.get("release_id")
    if not isinstance(release_id, str) or not release_id.strip():
        report.error(
            "READINESS_RELEASE_ID_INVALID",
            "release_id must be a non-empty string",
            "body.release_id",
        )
    try:
        uuid.UUID(str(body.get("instance_id")))
    except (ValueError, TypeError, AttributeError):
        report.error(
            "READINESS_INSTANCE_ID_INVALID",
            "instance_id must be a UUID",
            "body.instance_id",
        )
    if (
        not isinstance(body.get("generation"), int)
        or isinstance(body.get("generation"), bool)
        or body.get("generation", -1) < 0
    ):
        report.error(
            "READINESS_GENERATION_INVALID",
            "generation must be a non-negative integer fencing generation",
            "body.generation",
        )
    process_id = body.get("process_id")
    if (
        not isinstance(process_id, int)
        or isinstance(process_id, bool)
        or process_id <= 0
    ):
        report.error(
            "READINESS_PROCESS_ID_INVALID",
            "process_id must be a positive integer",
            "body.process_id",
        )

    _validate_endpoint(report, body.get("endpoint"))

    for digest_field in ("fund_lock_sha256", "service_manifest_sha256"):
        value = body.get(digest_field)
        if not isinstance(value, str) or HEX_64_RE.fullmatch(value) is None:
            report.error(
                "READINESS_DIGEST_INVALID",
                f"{digest_field} must be a resolved SHA-256 digest",
                f"body.{digest_field}",
            )

    authority = body.get("authority")
    if not isinstance(authority, dict):
        report.error(
            "READINESS_AUTHORITY_MISSING",
            "body.authority must be an object",
            "body.authority",
        )
    else:
        extra_authority_fields = sorted(
            set(authority) - {"broker", "mode", "execution_enabled"}
        )
        if extra_authority_fields:
            report.error(
                "READINESS_AUTHORITY_FIELDS_FORBIDDEN",
                f"unexpected readiness authority fields: {extra_authority_fields}",
                "body.authority",
            )
        expected_broker = {
            "DumbMoneyDummyKalshi": "KALSHI",
            "DumbMoneyDopeyRobinhood": "ROBINHOOD",
        }.get(service_name, "NONE")
        if authority.get("broker") != expected_broker:
            report.error(
                "READINESS_BROKER_AUTHORITY_MISMATCH",
                f"{service_name} must declare broker authority {expected_broker}",
                "body.authority.broker",
            )
        if authority.get("mode") not in {
            "OFFLINE",
            "RECONCILIATION_ONLY",
            "MECHANICAL_CANARY",
            "AGGRESSIVE_BOUNDED",
        }:
            report.error(
                "READINESS_AUTHORITY_MODE_INVALID",
                "authority.mode is not a recognized DumbMoney mode",
                "body.authority.mode",
            )
        execution_enabled = authority.get("execution_enabled")
        if not isinstance(execution_enabled, bool):
            report.error(
                "READINESS_EXECUTION_FLAG_INVALID",
                "authority.execution_enabled must be a boolean",
                "body.authority.execution_enabled",
            )
        elif expected_broker == "NONE" and execution_enabled:
            report.error(
                "READINESS_NON_VENUE_EXECUTION_FORBIDDEN",
                "non-venue services cannot declare execution enabled",
                "body.authority.execution_enabled",
            )
        elif (
            authority.get("mode") in {"OFFLINE", "RECONCILIATION_ONLY"}
            and execution_enabled
        ):
            report.error(
                "READINESS_MODE_EXECUTION_CONTRADICTION",
                "OFFLINE and RECONCILIATION_ONLY cannot declare execution enabled",
                "body.authority.execution_enabled",
            )

    health = body.get("health")
    if not isinstance(health, dict) or health.get("status") not in {
        "READY",
        "DEGRADED",
        "BLOCKED",
    }:
        report.error(
            "READINESS_HEALTH_INVALID",
            "health.status must be READY, DEGRADED, or BLOCKED",
            "body.health",
        )
    capabilities = body.get("capabilities")
    capabilities_valid = isinstance(capabilities, list)
    if capabilities_valid:
        capabilities_valid = all(
            isinstance(item, str) and bool(item) for item in capabilities
        )
    if capabilities_valid:
        capabilities_valid = len(set(capabilities)) == len(capabilities)
    if not capabilities_valid:
        report.error(
            "READINESS_CAPABILITIES_INVALID",
            "capabilities must be a unique array of non-empty strings",
            "body.capabilities",
        )

    try:
        not_before = parse_utc(descriptor.get("not_before"))
        expires_at = parse_utc(descriptor.get("expires_at"))
        observed_at = parse_utc(body.get("observed_at"))
        valid_until = parse_utc(body.get("valid_until"))
    except (TypeError, ValueError) as exc:
        report.error(
            "READINESS_TIME_INVALID",
            str(exc),
            "body.observed_at",
        )
    else:
        if expires_at <= not_before or expires_at - not_before > timedelta(seconds=120):
            report.error(
                "READINESS_ENVELOPE_WINDOW_INVALID",
                "signed envelope validity must be positive and no longer than 120 seconds",
                "expires_at",
            )
        if valid_until <= observed_at or valid_until - observed_at > timedelta(
            seconds=120
        ):
            report.error(
                "READINESS_TTL_INVALID",
                "readiness body validity must be positive and no longer than 120 seconds",
                "body.valid_until",
            )
        if valid_until != expires_at:
            report.error(
                "READINESS_EXPIRY_MISMATCH",
                "body.valid_until must equal signed envelope expires_at",
                "body.valid_until",
            )
        if not_before > observed_at:
            report.error(
                "READINESS_OBSERVED_BEFORE_VALID",
                "body.observed_at cannot precede signed envelope not_before",
                "body.observed_at",
            )
        if now_utc is not None and not not_before <= now_utc < expires_at:
            report.error(
                "READINESS_NOT_CURRENTLY_VALID",
                "readiness envelope is not currently valid",
                "expires_at",
            )

    algorithm = descriptor.get("signature_algorithm")
    if algorithm != "Ed25519":
        report.error(
            "READINESS_SIGNATURE_ALGORITHM_INVALID",
            "readiness envelopes must declare Ed25519",
            "signature_algorithm",
        )
    key_id = descriptor.get("signer_key_id")
    if not isinstance(key_id, str) or HEX_64_RE.fullmatch(key_id) is None:
        report.error(
            "READINESS_KEY_ID_INVALID",
            "signer_key_id must be the public-key SHA-256 digest",
            "signer_key_id",
        )
    signature_bytes = _decode_signature(report, descriptor.get("signature"))
    unsigned = dict(descriptor)
    unsigned.pop("signature", None)
    if signature_bytes is not None and verifier is not None:
        try:
            signing_bytes = canonical_json_bytes(unsigned)
            verified = verifier(
                signing_bytes,
                signature_bytes,
                str(key_id),
                str(algorithm),
            )
        except Exception as exc:
            report.error(
                "READINESS_SIGNATURE_VERIFIER_ERROR",
                f"signature verifier failed closed: {type(exc).__name__}",
                "signature",
            )
        else:
            if not verified:
                report.error(
                    "READINESS_SIGNATURE_REJECTED",
                    "readiness signature did not verify",
                    "signature",
                )
    elif signature_bytes is not None:
        report.partial(
            "READINESS_SIGNATURE_NOT_CRYPTOGRAPHICALLY_VERIFIED",
            "descriptor shape is valid but no trusted public-key verifier was supplied",
            "signature",
        )

    if require_parallel_contract:
        _validate_parallel_signed_envelope(report, descriptor)

    report.facts.update(
        {
            "service_name": service_name,
            "generation": body.get("generation"),
            "wire_schema": descriptor.get("schema"),
            "structural_signature_validation": True,
            "cryptographic_signature_validation": verifier is not None,
        }
    )
    return report
