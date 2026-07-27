from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    path: str = ""

    def as_dict(self) -> dict[str, str]:
        result = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.path:
            result["path"] = self.path
        return result


@dataclass
class ValidationReport:
    subject: str
    issues: list[ValidationIssue] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def error(self, code: str, message: str, path: str = "") -> None:
        self.issues.append(ValidationIssue("ERROR", code, message, path))

    def partial(self, code: str, message: str, path: str = "") -> None:
        self.issues.append(ValidationIssue("PARTIAL", code, message, path))

    def info(self, code: str, message: str, path: str = "") -> None:
        self.issues.append(ValidationIssue("INFO", code, message, path))

    def extend(self, issues: Iterable[ValidationIssue]) -> None:
        self.issues.extend(issues)

    @property
    def status(self) -> str:
        if any(issue.severity == "ERROR" for issue in self.issues):
            return "FAIL"
        if any(issue.severity == "PARTIAL" for issue in self.issues):
            return "PARTIAL"
        return "PASS"

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "status": self.status,
            "facts": self.facts,
            "issues": [issue.as_dict() for issue in self.issues],
        }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle, object_pairs_hook=_reject_duplicate_keys)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def canonical_json_bytes(value: Any) -> bytes:
    def validate_domain(item: Any, path: str = "$") -> None:
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            raise ValueError(
                f"{path} contains a float; signed and hashed contracts require integers"
            )
        if isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                validate_domain(child, f"{path}[{index}]")
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError(f"{path} contains a non-string object key")
                validate_domain(child, f"{path}.{key}")
            return
        raise ValueError(f"{path} contains unsupported type {type(item).__name__}")

    validate_domain(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str):
        raise ValueError("path must be a string")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or path.anchor:
        raise ValueError("path must be non-empty and relative")
    if re.match(r"^[A-Za-z]:", normalized):
        raise ValueError("drive-qualified paths are forbidden")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path traversal and empty path segments are forbidden")
    return path


def is_link_like(path: Path) -> bool:
    """Return true for symlinks and Windows directory junctions."""

    is_junction = getattr(os.path, "isjunction", None)
    return path.is_symlink() or bool(is_junction and is_junction(path))


def path_traverses_link(path: Path) -> bool:
    """Return true when an existing path component is a link or junction."""

    cursor = path
    while cursor != cursor.parent:
        if cursor.exists() and is_link_like(cursor):
            return True
        cursor = cursor.parent
    return False


def sanitized_child_environment(
    *executables: Path,
    temporary_directory: Path | None = None,
) -> dict[str, str]:
    """Build a minimal environment for pinned local build/verification tools."""

    path_directories: list[Path] = []
    for executable in executables:
        parent = executable.resolve(strict=False).parent
        for candidate in (parent, parent / "Scripts"):
            if candidate not in path_directories:
                path_directories.append(candidate)

    environment: dict[str, str] = {
        "PATH": os.pathsep.join(str(path) for path in path_directories),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "NUL" if os.name == "nt" else "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "",
        "GIT_TERMINAL_PROMPT": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
        "PYTHONUTF8": "1",
    }
    if os.name == "nt":
        raw_system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
        if raw_system_root:
            system_root = Path(raw_system_root).resolve(strict=False)
            system32 = system_root / "System32"
            for candidate in (system32, system_root):
                if candidate not in path_directories:
                    path_directories.append(candidate)
            environment.update(
                {
                    "SystemRoot": str(system_root),
                    "WINDIR": str(system_root),
                    "COMSPEC": str(system32 / "cmd.exe"),
                    "PATHEXT": ".COM;.EXE;.BAT;.CMD",
                    "PSModulePath": str(
                        system32 / "WindowsPowerShell" / "v1.0" / "Modules"
                    ),
                }
            )
            environment["PATH"] = os.pathsep.join(
                str(path) for path in path_directories
            )
    else:
        environment.update({"LANG": "C", "LC_ALL": "C"})

    if temporary_directory is not None:
        temporary = temporary_directory.resolve(strict=False)
        environment["TEMP"] = str(temporary)
        environment["TMP"] = str(temporary)
        if os.name != "nt":
            environment["TMPDIR"] = str(temporary)
    return environment


def resolve_beneath(root: Path, relative: str) -> Path:
    safe = safe_relative_path(relative)
    root_resolved = root.resolve()
    candidate = root_resolved.joinpath(*safe.parts)
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("path escapes its declared root") from exc
    cursor = root_resolved
    for part in safe.parts:
        cursor /= part
        if is_link_like(cursor):
            raise ValueError("symlinks and Windows junctions are forbidden")
    return candidate


def parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp must be an ISO-8601 UTC string ending in Z")
    return datetime.fromisoformat(value[:-1] + "+00:00")


def looks_unresolved(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    upper = stripped.upper()
    if not stripped:
        return True
    if stripped and set(stripped) == {"0"}:
        return True
    markers = (
        "${",
        "{{",
        "[[",
        "TO_BE_RESOLVED",
        "REPLACE_ME",
        "UNRESOLVED",
        "REQUIRED_VALUE",
    )
    return any(marker in upper for marker in markers)


def write_new_json(path: Path, value: Any) -> None:
    """Write a new evidence file without overwriting an existing artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def validate_hex_digest(
    report: ValidationReport,
    value: Any,
    *,
    field_path: str,
    length: int = 64,
    unresolved_is_partial: bool = True,
) -> bool:
    if looks_unresolved(value):
        method = report.partial if unresolved_is_partial else report.error
        method(
            "UNRESOLVED_DIGEST",
            f"{field_path} must be resolved before release",
            field_path,
        )
        return False
    pattern = HEX_64_RE if length == 64 else HEX_40_RE
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        report.error(
            "INVALID_DIGEST",
            f"{field_path} must be {length} lowercase hexadecimal characters",
            field_path,
        )
        return False
    return True
