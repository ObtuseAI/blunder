"""Ed25519 signing and verification for DumbMoney envelopes.

Private key material is accepted only as an in-memory object.  Contracts,
configuration, ledgers, and public keyrings contain public keys or key IDs
only.
"""

from __future__ import annotations

import base64
import binascii
import hmac
from dataclasses import dataclass
from typing import Mapping, Protocol

from blunder.fund.canonical import bytes_sha256, require_digest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


ED25519_ALGORITHM = "Ed25519"


class SignatureBackendUnavailable(RuntimeError):
    """Raised when the required Ed25519 implementation is unavailable."""


class SignatureVerificationError(ValueError):
    """Raised when an envelope signature or signer identity is invalid."""


class EnvelopeSigner(Protocol):
    @property
    def algorithm(self) -> str: ...

    @property
    def key_id(self) -> str: ...

    def sign(self, payload: bytes) -> str: ...


def _require_backend() -> None:
    # Kept as an explicit call site guard so alternate packaging failures are
    # reported at import time by the required bounded dependency.
    return


def encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_base64url(value: str, context: str) -> bytes:
    if not isinstance(value, str) or not value or "=" in value:
        raise ValueError(f"{context} must be unpadded URL-safe base64")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"{context} must be valid URL-safe base64") from exc
    if encode_base64url(decoded) != value:
        raise ValueError(f"{context} must use canonical unpadded URL-safe base64")
    return decoded


@dataclass(frozen=True)
class Ed25519Signer:
    """In-memory Ed25519 signer suitable for an OS-backed key provider."""

    _private_key: Ed25519PrivateKey

    @classmethod
    def generate(cls) -> "Ed25519Signer":
        _require_backend()
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_private_bytes(cls, raw: bytes) -> "Ed25519Signer":
        _require_backend()
        if len(raw) != 32:
            raise ValueError("Ed25519 private key seed must be exactly 32 bytes")
        return cls(Ed25519PrivateKey.from_private_bytes(raw))

    @property
    def algorithm(self) -> str:
        return ED25519_ALGORITHM

    @property
    def public_key_bytes(self) -> bytes:
        _require_backend()
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def public_key_base64url(self) -> str:
        return encode_base64url(self.public_key_bytes)

    @property
    def key_id(self) -> str:
        return bytes_sha256(self.public_key_bytes)

    def sign(self, payload: bytes) -> str:
        _require_backend()
        return encode_base64url(self._private_key.sign(payload))


class Ed25519Keyring:
    """Explicit public-key registry; unknown key IDs always fail closed."""

    def __init__(self, keys: Mapping[str, bytes] | None = None) -> None:
        self._keys: dict[str, Ed25519PublicKey] = {}
        for key_id, raw in (keys or {}).items():
            self.register(raw, expected_key_id=key_id)

    def register(self, raw_public_key: bytes, expected_key_id: str | None = None) -> str:
        _require_backend()
        if len(raw_public_key) != 32:
            raise ValueError("Ed25519 public key must be exactly 32 bytes")
        key_id = bytes_sha256(raw_public_key)
        if expected_key_id is not None:
            require_digest(expected_key_id, "expected_key_id")
            if not hmac.compare_digest(key_id, expected_key_id):
                raise SignatureVerificationError("public key does not match expected key ID")
        existing = self._keys.get(key_id)
        public_key = Ed25519PublicKey.from_public_bytes(raw_public_key)
        if existing is not None:
            existing_raw = existing.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            if not hmac.compare_digest(existing_raw, raw_public_key):
                raise SignatureVerificationError("key ID collision detected")
        self._keys[key_id] = public_key
        return key_id

    def verify(self, key_id: str, algorithm: str, payload: bytes, signature: str) -> None:
        _require_backend()
        require_digest(key_id, "signer_key_id")
        if algorithm != ED25519_ALGORITHM:
            raise SignatureVerificationError(f"unsupported signature algorithm: {algorithm}")
        public_key = self._keys.get(key_id)
        if public_key is None:
            raise SignatureVerificationError(f"unknown signer key ID: {key_id}")
        raw_signature = decode_base64url(signature, "signature")
        if len(raw_signature) != 64:
            raise SignatureVerificationError("Ed25519 signature must decode to exactly 64 bytes")
        try:
            public_key.verify(raw_signature, payload)
        except InvalidSignature as exc:
            raise SignatureVerificationError("Ed25519 signature verification failed") from exc

    def contains(self, key_id: str) -> bool:
        return key_id in self._keys
