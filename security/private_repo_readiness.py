from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from obtuse_secret_hygiene import Finding, scan_text


REQUIRED_FILES: tuple[str, ...] = (
    ".gitattributes",
    ".gitignore",
    "README.md",
    "SECURITY.md",
    "docs/blunder-focus-shell.png",
    "pyproject.toml",
)

BLOCKED_PATHS: tuple[str, ...] = (
    "configs/obtuse/blunder-operator-autonomy-authorization.json",
)

FORBIDDEN_AUTHENTICITY_MARKERS: tuple[str, ...] = (
    "ki" + "lo",
    "open" + "code",
    "third" + "_" + "party",
    "third" + "-" + "party",
    "leg" + "acy",
    "doo" + "fus",
    "mini" + "sweagent",
    "mini" + "-" + "swe",
    "swe" + "-" + "agent",
    "do" + "pey",
)

BLOCKED_PREFIXES: tuple[str, ...] = (
    ".venv/",
    ".venv-blunder-shell/",
    "artifacts/",
)

BLOCKED_PARTS: frozenset[str] = frozenset(
    {
        "__pycache__",
        "node_modules",
        ".bun",
        "dist",
    }
)

TEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        ".cfg",
        ".css",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".jsonl",
        ".md",
        ".ps1",
        ".psm1",
        ".py",
        ".sh",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)

MAX_TEXT_SCAN_BYTES = 10 * 1024 * 1024
MAX_REPOSITORY_FILE_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class Blocker:
    kind: str
    path: str
    detail: str


def run_git(root: Path, arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def collect_candidate_paths(root: Path) -> list[Path]:
    result = run_git(root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"failed to enumerate Git candidate files in {root}: {error}")
    relative_paths = [item for item in result.stdout.decode("utf-8", errors="strict").split("\0") if item]
    return [root / Path(item) for item in sorted(relative_paths)]


def normalize_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def path_policy_blockers(paths: Iterable[Path], root: Path) -> list[Blocker]:
    blockers: list[Blocker] = []
    for path in paths:
        relative = normalize_relative(path, root)
        parts = set(Path(relative).parts)
        if relative in BLOCKED_PATHS:
            blockers.append(Blocker("local_only_file", relative, "local authorization or machine configuration"))
        if any(relative.startswith(prefix) for prefix in BLOCKED_PREFIXES):
            blockers.append(Blocker("generated_path", relative, "runtime or generated evidence path"))
        if parts.intersection(BLOCKED_PARTS):
            blockers.append(Blocker("generated_path", relative, "dependency, cache, or compiled output path"))
    return blockers


def required_file_blockers(root: Path) -> list[Blocker]:
    return [
        Blocker("missing_required_file", relative, "required private-repository control is missing")
        for relative in REQUIRED_FILES
        if not (root / relative).is_file()
    ]


def size_blockers(paths: Iterable[Path], root: Path) -> list[Blocker]:
    blockers: list[Blocker] = []
    for path in paths:
        try:
            size = path.stat().st_size
        except OSError as error:
            relative = normalize_relative(path, root)
            raise RuntimeError(f"failed to inspect candidate file {relative}: {error}") from error
        if size > MAX_REPOSITORY_FILE_BYTES:
            blockers.append(
                Blocker(
                    "oversized_file",
                    normalize_relative(path, root),
                    f"{size} bytes exceeds the {MAX_REPOSITORY_FILE_BYTES}-byte repository limit",
                )
            )
    return blockers


def authenticity_blockers(paths: Iterable[Path], root: Path) -> list[Blocker]:
    blockers: list[Blocker] = []
    for path in paths:
        relative = normalize_relative(path, root)
        lowered_path = relative.lower()
        for marker in FORBIDDEN_AUTHENTICITY_MARKERS:
            if marker in lowered_path:
                blockers.append(Blocker("authenticity_path", relative, f"retired compatibility marker: {marker}"))
        if path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > MAX_TEXT_SCAN_BYTES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for marker in FORBIDDEN_AUTHENTICITY_MARKERS:
            if marker in text:
                blockers.append(Blocker("authenticity_content", relative, f"retired compatibility marker: {marker}"))
    return blockers


def secret_findings(paths: Iterable[Path], root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if path.stat().st_size > MAX_TEXT_SCAN_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as error:
            relative = normalize_relative(path, root)
            raise RuntimeError(f"failed to read candidate file {relative}: {error}") from error
        findings.extend(scan_text(path, root, text))
    return findings


def origin_policy(root: Path) -> dict[str, str | bool]:
    result = run_git(root, ["remote", "get-url", "origin"])
    if result.returncode != 0:
        return {
            "configured": False,
            "url": "",
            "organization_match": False,
            "status": "UNCONFIGURED_REVIEW_REQUIRED",
        }
    url = result.stdout.decode("utf-8", errors="strict").strip()
    organization_match = "github.com/obtuseai/" in url.lower() or "github.com:obtuseai/" in url.lower()
    return {
        "configured": True,
        "url": url,
        "organization_match": organization_match,
        "status": "PASS" if organization_match else "BLOCKED_WRONG_ORGANIZATION",
    }


def github_visibility(root: Path, origin: dict[str, str | bool]) -> dict[str, str | bool]:
    if origin["configured"] is not True:
        return {"verified": False, "private": False, "visibility": "UNVERIFIED", "repository": ""}
    completed = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner,visibility,isPrivate,url"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        return {"verified": False, "private": False, "visibility": "UNVERIFIED", "repository": ""}
    value = json.loads(completed.stdout.decode("utf-8", errors="strict"))
    private = value.get("isPrivate") is True and str(value.get("visibility") or "").upper() == "PRIVATE"
    return {
        "verified": True,
        "private": private,
        "visibility": str(value.get("visibility") or "UNVERIFIED").upper(),
        "repository": str(value.get("nameWithOwner") or ""),
        "url": str(value.get("url") or ""),
    }


def write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_report(root: Path) -> dict[str, object]:
    paths = collect_candidate_paths(root)
    blockers = [
        *required_file_blockers(root),
        *path_policy_blockers(paths, root),
        *size_blockers(paths, root),
        *authenticity_blockers(paths, root),
    ]
    secrets = secret_findings(paths, root)
    origin = origin_policy(root)
    visibility = github_visibility(root, origin)
    if bool(origin["configured"]) and not bool(origin["organization_match"]):
        blockers.append(Blocker("remote_policy", "origin", "origin is not in the obtuseai GitHub organization"))
    if bool(origin["configured"]) and (visibility["verified"] is not True or visibility["private"] is not True):
        blockers.append(Blocker("visibility_policy", "origin", "GitHub repository visibility is not verified private"))
    if secrets:
        blockers.append(Blocker("secret_signature", "multiple", f"{len(secrets)} strong secret signature(s) found"))
    ready = not blockers
    total_bytes = sum(path.stat().st_size for path in paths)
    return {
        "schema": "obtuse.blunder.private_repo_readiness.v1",
        "status": "READY_FOR_PRIVATE_GITHUB_REVIEW" if ready else "BLOCKED",
        "commit_allowed_after_human_diff_review": ready,
        "root": str(root),
        "candidate_file_count": len(paths),
        "candidate_total_bytes": total_bytes,
        "blocker_count": len(blockers),
        "blockers": [asdict(blocker) for blocker in blockers],
        "secret_finding_count": len(secrets),
        "secret_findings": [asdict(finding) for finding in secrets],
        "origin": origin,
        "required_visibility": "PRIVATE",
        "required_organization": "obtuseai",
        "visibility_verified": visibility["verified"] is True and visibility["private"] is True,
        "visibility": visibility,
        "visibility_note": "GitHub visibility must be PRIVATE before every push.",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail-closed readiness check for the private Blunder GitHub repository.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    report_path = Path(args.report).resolve()
    if not (root / ".git").exists():
        raise RuntimeError(f"private repository readiness requires an initialized Git worktree: {root}")
    report = build_report(root)
    write_report(report_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "READY_FOR_PRIVATE_GITHUB_REVIEW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
