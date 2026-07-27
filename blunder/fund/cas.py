"""Content-addressed evidence store for DumbMoney control artifacts."""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

from blunder.fund.canonical import bytes_sha256, canonical_json_bytes, require_digest


class ContentIntegrityError(ValueError):
    """Raised when bytes do not match their content address."""


class ContentAddressedStore:
    """A write-once SHA-256 object store with atomic publication."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def path_for(self, digest: str) -> Path:
        require_digest(digest, "digest")
        return self.root / "sha256" / digest[:2] / digest

    def put_bytes(self, payload: bytes) -> str:
        if not isinstance(payload, bytes):
            raise TypeError("CAS payload must be bytes")
        digest = bytes_sha256(payload)
        destination = self.path_for(digest)
        with self._lock:
            if destination.is_file():
                if destination.read_bytes() != payload:
                    raise ContentIntegrityError(f"existing CAS object does not match its address: {digest}")
                return digest
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{digest}.", dir=destination.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                if bytes_sha256(temporary.read_bytes()) != digest:
                    raise ContentIntegrityError("temporary CAS object changed before publication")
                os.replace(temporary, destination)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return digest

    def put_json(self, value: object) -> str:
        return self.put_bytes(canonical_json_bytes(value))

    def get_bytes(self, digest: str) -> bytes:
        path = self.path_for(digest)
        if not path.is_file():
            raise FileNotFoundError(f"CAS object is missing: {digest}")
        payload = path.read_bytes()
        if bytes_sha256(payload) != digest:
            raise ContentIntegrityError(f"CAS object failed digest verification: {digest}")
        return payload

    def has(self, digest: str) -> bool:
        try:
            self.get_bytes(digest)
        except FileNotFoundError:
            return False
        return True
