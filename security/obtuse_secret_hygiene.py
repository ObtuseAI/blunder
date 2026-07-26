from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "openrouter": re.compile(r"\bsk-or-v1-[A-Za-z0-9_-]{24,}\b"),
    "openai_project": re.compile(r"\bsk-proj-[A-Za-z0-9_-]{24,}\b"),
    "openai": re.compile(r"\bsk-(?!proj-)[A-Za-z0-9_-]{32,}\b"),
    "github": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}

SAFE_PLACEHOLDERS: tuple[str, ...] = (
    "${OPENROUTER_API_KEY}",
    "%OPENROUTER_API_KEY%",
    "$env:OPENROUTER_API_KEY",
    "<OPENROUTER_API_KEY>",
    "${OPENAI_API_KEY}",
    "%OPENAI_API_KEY%",
    "$env:OPENAI_API_KEY",
    "<OPENAI_API_KEY>",
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
    "sk-abcdefghijklmnopqrstuvwxyz123456",
    "sk-proj-abcdefghijklmnopqrstuvwxyz_1234567890",
)

SCAN_SUFFIXES: set[str] = {
    ".ps1",
    ".psm1",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".jsonl",
    ".md",
    ".yaml",
    ".yml",
    ".sh",
    ".txt",
    ".log",
}

SKIP_DIRS: set[str] = {
    ".git",
    ".venv",
    ".venv-blunder-shell",
    "__pycache__",
    "node_modules",
    ".bun",
}

MAX_SCAN_BYTES = 10 * 1024 * 1024

ROOT_INCLUDE_DIRS = {
    "scripts",
    "benchmarks",
    "blunder",
    "configs",
    "docs",
    "tests",
    "security",
    "artifacts",
}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    digest: str
    redacted: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(value: str) -> str:
    prefix = value[:8]
    suffix = value[-4:]
    return f"{prefix}...{suffix}"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_safe_placeholder(value: str, line: str) -> bool:
    if value in SAFE_PLACEHOLDERS:
        return True
    return value == "-----BEGIN OPENSSH PRIVATE KEY-----" and "MIIB...===" in line


def should_scan(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SCAN_SUFFIXES


def iter_files(root: Path) -> Iterable[Path]:
    for base, dirs, files in os.walk(root):
        dirs[:] = [item for item in dirs if item not in SKIP_DIRS]
        parent = Path(base)
        rel = parent.relative_to(root)
        parts = rel.parts
        if not parts:
            dirs[:] = [item for item in dirs if item in ROOT_INCLUDE_DIRS]
        elif parts == ("artifacts",):
            dirs[:] = [item for item in dirs if item == "obtuse"]
        elif parts == ("artifacts", "obtuse"):
            dirs[:] = [
                item
                for item in dirs
                if item
                in {
                    "real-capability-compounding-v2",
                    "targeted-reward0-family-delta-gates-v1",
                      "targeted-reward0-operator-approved-single-task-smokes-v1",
                      "targeted-reward0-approval-packet-handoff-v1",
                      "targeted-reward0-operator-manual-approval-input-create-v1",
                      "targeted-reward0-operator-intent-manual-fill-v1",
                      "targeted-reward0-operator-source-intent-create-v1",
              "targeted-reward0-source-create-request-manual-fill-v1",
              "targeted-reward0-source-create-request-source-manual-fill-v1",
              "targeted-reward0-operator-action-required-create-request-source-file-v1",
              "blunder-claude-audit-fix-2026-07-05_1143",
                  }
              ]
        for name in files:
            path = parent / name
            if should_scan(path) and path.stat().st_size <= MAX_SCAN_BYTES:
                yield path


def scan_text(path: Path, root: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(line):
                value = match.group(0)
                if is_safe_placeholder(value, line):
                    continue
                findings.append(
                    Finding(
                        path=str(path.relative_to(root)),
                        line=number,
                        kind=kind,
                        digest=digest(value),
                        redacted=redact(value),
                    )
                )
    return findings


def scan_root(root: Path) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    scanned = 0
    for path in iter_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            raise RuntimeError(f"failed to read scan target {path}: {exc}") from exc
        scanned += 1
        findings.extend(scan_text(path, root, text))
    return findings, scanned


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan Obtuse active files for secret-shaped tokens.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--artifact-root", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    artifact = Path(args.artifact_root).resolve()
    findings, scanned = scan_root(root)
    summary = {
        "schema": "obtuse.secret_hygiene.summary.v1",
        "timestamp": utc_now(),
        "root": str(root),
        "files_scanned": scanned,
        "finding_count": len(findings),
        "status": "SECRET_HYGIENE_TERMINAL_BLOCK" if findings else "PASS",
        "safe_placeholders_ignored": list(SAFE_PLACEHOLDERS),
        "openrouter_rotation_recommendation": "OPENROUTER_KEY_ROTATION_STILL_RECOMMENDED_IF_PRIOR_ARTIFACTS_EXPOSED_KEY",
        "secret_values_printed": False,
    }
    rows = [finding.__dict__ for finding in findings]
    write_json(artifact / "secret_hygiene_summary.json", summary)
    write_jsonl(artifact / "secret_hygiene_findings.jsonl", rows)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 2 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
