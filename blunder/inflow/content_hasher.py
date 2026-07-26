from __future__ import annotations

from pathlib import Path
import hashlib


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_text(text: str) -> str:
    return hash_bytes(text.encode("utf-8", errors="replace"))


def hash_file(path: Path) -> str:
    return hash_bytes(path.read_bytes())


def read_candidate_bytes(path_text: str, inline_text: str) -> bytes:
    if path_text:
        return Path(path_text).read_bytes()
    return inline_text.encode("utf-8", errors="replace")
