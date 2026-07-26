from __future__ import annotations

import hashlib
import hmac
import json
import re
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from blunder.constitutional_adapters import record_release_governance_boundaries


RELEASE_SCHEMA = "obtuse.blunder.private-release-governance.v1"
SCHEMA_PATTERN = re.compile(r"obtuse\.blunder\.[a-z0-9._-]+\.v\d+", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_text(root: Path, arguments: Sequence[str]) -> str:
    command = ("git", "-C", str(root), *arguments)
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Git release probe failed: {completed.stderr[-500:]}")
    return completed.stdout.strip()


def repository_version(root: Path) -> str:
    value = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = value.get("project")
    version = str(project.get("version") or "") if isinstance(project, dict) else ""
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version):
        raise ValueError("Project version is missing or not semantic.")
    return version


def tracked_paths(root: Path) -> tuple[str, ...]:
    return tuple(path for path in git_text(root, ("ls-files",)).splitlines() if path)


def tree_digest(root: Path, paths: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(paths):
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"Tracked release path is missing: {relative}")
        digest.update(relative.replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest().upper()


def schema_inventory(root: Path, paths: Sequence[str]) -> tuple[str, ...]:
    observed: set[str] = set()
    for relative in paths:
        path = root / relative
        if path.suffix.lower() not in {".json", ".md", ".ps1", ".py", ".yaml", ".yml"}:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        observed.update(match.group(0) for match in SCHEMA_PATTERN.finditer(content))
    return tuple(sorted(observed))


def unsigned_payload(manifest: Mapping[str, object]) -> bytes:
    payload = {key: value for key, value in manifest.items() if key not in {"signature", "manifestSha256"}}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_manifest(manifest: Mapping[str, object], signing_key: str) -> str:
    if len(signing_key) < 32:
        raise ValueError("Release signing key must contain at least 32 characters.")
    return hmac.new(signing_key.encode("utf-8"), unsigned_payload(manifest), hashlib.sha256).hexdigest().upper()


def build_release_manifest(
    root: Path,
    readiness: Mapping[str, object],
    continuous_proof: Mapping[str, object],
    required_checks: Sequence[Mapping[str, object]],
    signing_key: str | None,
) -> dict[str, object]:
    resolved = root.resolve()
    head = git_text(resolved, ("rev-parse", "HEAD"))
    status = git_text(resolved, ("status", "--porcelain"))
    paths = tracked_paths(resolved)
    check_rows = [dict(item) for item in required_checks]
    checks_passed = bool(check_rows) and all(str(item.get("conclusion") or "").upper() == "SUCCESS" for item in check_rows)
    proof_commit = str(continuous_proof.get("commit") or continuous_proof.get("baseCommit") or "")
    gates = {
        "worktreeClean": not status,
        "privateReadinessPassed": readiness.get("status") == "READY_FOR_PRIVATE_GITHUB_REVIEW",
        "visibilityPrivate": ((readiness.get("visibility") or {}).get("private") is True)
        if isinstance(readiness.get("visibility"), dict)
        else False,
        "continuousProofPassed": continuous_proof.get("status") == "PASS",
        "proofMatchesHead": proof_commit == head,
        "requiredChecksPassed": checks_passed,
        "versionValid": bool(repository_version(resolved)),
    }
    constitutional_decisions = record_release_governance_boundaries(resolved, datetime.now(timezone.utc))
    manifest: dict[str, object] = {
        "schema": RELEASE_SCHEMA,
        "createdAt": utc_now(),
        "repository": "ObtuseAI/blunder",
        "privateOnly": True,
        "version": repository_version(resolved),
        "commit": head,
        "treeSha256": tree_digest(resolved, paths),
        "trackedFiles": len(paths),
        "schemas": list(schema_inventory(resolved, paths)),
        "gates": gates,
        "checks": check_rows,
        "proof": {
            "status": continuous_proof.get("status"),
            "commit": proof_commit,
            "sourceTreeSha256": continuous_proof.get("sourceTreeSha256"),
        },
        "authority": {
            "mergeAuthorized": False,
            "releaseAuthorized": False,
            "publicReleaseAuthorized": False,
            "requiresExactOperatorConfirmation": True,
        },
        "constitutionalDecisions": constitutional_decisions,
        "signatureAlgorithm": "HMAC-SHA256" if signing_key else None,
        "signature": None,
    }
    manifest["status"] = (
        "READY_FOR_EXACT_OPERATOR_CONFIRMATION"
        if all(gates.values()) and signing_key
        else "BLOCKED_MISSING_SIGNING_KEY"
        if all(gates.values())
        else "BLOCKED_RELEASE_GATES"
    )
    manifest["signature"] = sign_manifest(manifest, signing_key) if signing_key else None
    manifest["manifestSha256"] = hashlib.sha256(unsigned_payload(manifest)).hexdigest().upper()
    return manifest


def verify_release_manifest(manifest: Mapping[str, object], signing_key: str) -> dict[str, object]:
    observed_signature = str(manifest.get("signature") or "")
    expected_signature = sign_manifest(manifest, signing_key)
    observed_digest = str(manifest.get("manifestSha256") or "")
    expected_digest = hashlib.sha256(unsigned_payload(manifest)).hexdigest().upper()
    gates = {
        "schemaValid": manifest.get("schema") == RELEASE_SCHEMA,
        "privateOnly": manifest.get("privateOnly") is True,
        "signatureValid": hmac.compare_digest(observed_signature, expected_signature),
        "manifestDigestValid": hmac.compare_digest(observed_digest, expected_digest),
        "releaseGatesPassed": all(bool(value) for value in (manifest.get("gates") or {}).values())
        if isinstance(manifest.get("gates"), dict)
        else False,
    }
    return {
        "schema": "obtuse.blunder.private-release-verification.v1",
        "status": "PASS" if all(gates.values()) else "FAIL",
        "gates": gates,
        "commit": manifest.get("commit"),
        "version": manifest.get("version"),
    }


def write_release_manifest(path: Path, manifest: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(manifest), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path
