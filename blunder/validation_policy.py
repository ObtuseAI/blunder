from __future__ import annotations


VALIDATION_GUARD_SOURCE = r'''from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import threading


class BlunderValidationBoundaryError(PermissionError):
    pass


def _trusted_callers() -> dict[str, str]:
    value = json.loads(os.environ.get("BLUNDER_VALIDATION_TRUSTED_CALLERS", "{}"))
    return {str(Path(path).resolve()).lower(): str(digest) for path, digest in value.items()}


TRUSTED_CALLERS = _trusted_callers()
WORKSPACE_ROOT = Path(os.environ["BLUNDER_VALIDATION_WORKSPACE"]).resolve()
PYTHON_ROOTS = tuple({Path(sys.base_prefix).resolve(), Path(sys.prefix).resolve()})
GUARD_FILE = Path(__file__).resolve()
AUDIT_STATE = threading.local()
DIGEST_CACHE = {}


def _sha256(path: Path) -> str:
    stat = path.stat()
    key = (str(path).lower(), stat.st_mtime_ns, stat.st_size)
    cached = DIGEST_CACHE.get(key)
    if cached is not None:
        return cached
    value = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    if len(DIGEST_CACHE) > 1024:
        DIGEST_CACHE.clear()
    DIGEST_CACHE[key] = value
    return value


def _repository_frames() -> list[Path]:
    repository_frames = []
    frame = sys._getframe(2)
    while frame is not None:
        filename = frame.f_code.co_filename
        frame = frame.f_back
        if filename.startswith("<") and filename.endswith(">"):
            continue
        path = Path(filename).resolve()
        if path.is_relative_to(WORKSPACE_ROOT) and path != GUARD_FILE:
            repository_frames.append(path)
    return repository_frames


def _repository_frames_are_trusted() -> bool:
    repository_frames = _repository_frames()
    if not repository_frames:
        return False
    return all(
        TRUSTED_CALLERS.get(str(path).lower()) == _sha256(path)
        for path in repository_frames
        if path.is_file()
    )


def _untrusted_repository_frames() -> list[Path]:
    frames = _repository_frames()
    return [
        path
        for path in frames
        if not path.is_file() or TRUSTED_CALLERS.get(str(path).lower()) != _sha256(path)
    ]


def _safe_external_import(path: Path) -> bool:
    return any(path.is_relative_to(root) for root in PYTHON_ROOTS) and path.suffix.lower() in {
        ".dll", ".py", ".pyc", ".pyd", ".zip"
    }


def _loopback_host(value: object) -> bool:
    host = str(value).strip().lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _loopback_address(address: object) -> bool:
    if isinstance(address, str):
        return True
    return isinstance(address, tuple) and bool(address) and _loopback_host(address[0])


def _audit_impl(event: str, args: tuple[object, ...]) -> None:
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(args[0]).resolve()
        if path.is_relative_to(WORKSPACE_ROOT) or _safe_external_import(path):
            return
        untrusted_frames = _untrusted_repository_frames()
        if untrusted_frames:
            callers = ",".join(str(item.relative_to(WORKSPACE_ROOT)) for item in untrusted_frames[:4])
            raise BlunderValidationBoundaryError(
                f"Generated validation code cannot read or write outside its worktree; untrustedCallers={callers}."
            )
    if event in {"os.listdir", "os.scandir"} and args:
        path = Path(args[0]).resolve()
        if path.is_relative_to(WORKSPACE_ROOT) or any(path.is_relative_to(root) for root in PYTHON_ROOTS):
            return
        if _untrusted_repository_frames():
            raise BlunderValidationBoundaryError("Generated validation code cannot enumerate outside its worktree.")
    if event == "socket.getaddrinfo" and (not args or not _loopback_host(args[0])):
        raise BlunderValidationBoundaryError("Generated validation code cannot resolve external network hosts.")
    if event == "socket.connect" and (len(args) < 2 or not _loopback_address(args[1])):
        raise BlunderValidationBoundaryError("Generated validation code cannot access non-loopback networks.")
    if event in {"subprocess.Popen", "os.system"} or event.startswith("os.spawn"):
        if not _repository_frames_are_trusted():
            raise BlunderValidationBoundaryError("Generated validation code cannot start child processes.")
    if event.startswith("ctypes.") and not _repository_frames_are_trusted():
        raise BlunderValidationBoundaryError("Generated validation code cannot load native libraries.")


def _audit(event: str, args: tuple[object, ...]) -> None:
    if getattr(AUDIT_STATE, "active", False):
        return
    AUDIT_STATE.active = True
    try:
        _audit_impl(event, args)
    finally:
        AUDIT_STATE.active = False


sys.addaudithook(_audit)
'''


SENSITIVE_ENVIRONMENT_MARKERS: tuple[str, ...] = (
    "API_KEY",
    "AUTH",
    "BEARER",
    "CREDENTIAL",
    "PASSWORD",
    "SECRET",
    "TOKEN",
)

VALIDATION_ENVIRONMENT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
)
