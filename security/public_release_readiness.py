from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final
from urllib.parse import unquote


TEXT_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        ".cff",
        ".css",
        ".html",
        ".js",
        ".json",
        ".md",
        ".ps1",
        ".py",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)
REQUIRED_FILES: Final[tuple[str, ...]] = (
    "README.md",
    "LICENSE",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CITATION.cff",
)
PRIVATE_PATH_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"C:(?:\\+|/+)Users(?:\\+|/+)(?:CH|chris)(?:(?:\\+|/+)|$)", re.IGNORECASE),
    re.compile(r"(?:\\+|/+)FRANKENSTEIN(?:\\+|/+)Shared Folder", re.IGNORECASE),
)
STALE_POSTURE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bprivate repository\b", re.IGNORECASE),
    re.compile(r"\bdo not publish\b", re.IGNORECASE),
    re.compile(r"\bprivate alpha\b", re.IGNORECASE),
    re.compile(r"<private-repository-url>", re.IGNORECASE),
)
MARKDOWN_LINK_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:!\[[^\]]*\]|\[[^\]]+\])\(([^)]+)\)"
)


@dataclass(frozen=True)
class Blocker:
    code: str
    path: str
    detail: str


def run_git(root: Path, arguments: tuple[str, ...]) -> tuple[str, ...]:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return tuple(line for line in completed.stdout.splitlines() if line)


def tracked_files(root: Path) -> tuple[Path, ...]:
    relative_paths = run_git(root, ("ls-files",))
    return tuple(root / relative_path for relative_path in relative_paths)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def find_required_file_blockers(root: Path) -> tuple[Blocker, ...]:
    return tuple(
        Blocker("missing_required_file", relative_path, "required release file is missing")
        for relative_path in REQUIRED_FILES
        if not (root / relative_path).is_file()
    )


def find_private_path_blockers(root: Path, paths: tuple[Path, ...]) -> tuple[Blocker, ...]:
    blockers: list[Blocker] = []
    for path in paths:
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = read_text(path)
        for pattern in PRIVATE_PATH_PATTERNS:
            if pattern.search(text) is not None:
                blockers.append(
                    Blocker(
                        "private_path",
                        path.relative_to(root).as_posix(),
                        f"matched forbidden public-tree path pattern: {pattern.pattern}",
                    )
                )
    return tuple(blockers)


def find_readme_blockers(root: Path) -> tuple[Blocker, ...]:
    readme_path = root / "README.md"
    if not readme_path.is_file():
        return ()
    text = read_text(readme_path)
    blockers: list[Blocker] = []
    if text.count("```mermaid") < 2:
        blockers.append(
            Blocker("presentation", "README.md", "README must contain at least two Mermaid diagrams")
        )
    if re.search(r"!\[[^\]]*\]\([^)]+\)", text) is None:
        blockers.append(
            Blocker("presentation", "README.md", "README must contain a project visual")
        )
    if text.count("```") % 2 != 0:
        blockers.append(Blocker("markdown", "README.md", "README code fences are unbalanced"))
    for pattern in STALE_POSTURE_PATTERNS:
        if pattern.search(text) is not None:
            blockers.append(
                Blocker(
                    "stale_private_posture",
                    "README.md",
                    f"matched stale public-release language: {pattern.pattern}",
                )
            )
    blockers.extend(find_broken_readme_links(root, text))
    return tuple(blockers)


def normalize_link_target(value: str) -> str:
    target = unquote(value.strip())
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    return target.split("#", maxsplit=1)[0]


def find_broken_readme_links(root: Path, text: str) -> tuple[Blocker, ...]:
    blockers: list[Blocker] = []
    for match in MARKDOWN_LINK_PATTERN.finditer(text):
        target = normalize_link_target(match.group(1))
        if (
            not target
            or target.startswith(("http://", "https://", "mailto:", "#"))
            or "?" in target
        ):
            continue
        candidate = root / target.replace("/", str(Path("/"))).lstrip("/")
        if not candidate.exists():
            blockers.append(
                Blocker("broken_readme_link", "README.md", f"missing local target: {target}")
            )
    return tuple(blockers)


def find_policy_blockers(root: Path) -> tuple[Blocker, ...]:
    blockers: list[Blocker] = []
    license_text = read_text(root / "LICENSE") if (root / "LICENSE").is_file() else ""
    if re.search(r"\bconfidential\b|\bdo not publish\b", license_text, re.IGNORECASE):
        blockers.append(
            Blocker("license_posture", "LICENSE", "license still contains private-only language")
        )
    security_text = (
        read_text(root / "SECURITY.md") if (root / "SECURITY.md").is_file() else ""
    )
    if "Report a vulnerability" not in security_text:
        blockers.append(
            Blocker(
                "security_reporting",
                "SECURITY.md",
                "private vulnerability reporting instructions are missing",
            )
        )
    workflow_candidates = (
        root / ".github" / "workflows" / "quality.yml",
        root / ".github" / "workflows" / "tests.yml",
    )
    if not any(path.is_file() for path in workflow_candidates):
        blockers.append(
            Blocker("workflow", ".github/workflows", "no public quality workflow is present")
        )
    return tuple(blockers)


def build_report(root: Path) -> dict[str, object]:
    paths = tracked_files(root)
    blockers = (
        *find_required_file_blockers(root),
        *find_private_path_blockers(root, paths),
        *find_readme_blockers(root),
        *find_policy_blockers(root),
    )
    head = run_git(root, ("rev-parse", "HEAD"))[0]
    return {
        "schema": "obtuse.public-release-readiness.v1",
        "project": root.name,
        "status": "READY_FOR_PUBLIC_SOURCE_PREVIEW" if not blockers else "BLOCKED",
        "head": head,
        "tracked_file_count": len(paths),
        "blocker_count": len(blockers),
        "blockers": [asdict(blocker) for blocker in blockers],
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fail-closed public source-preview readiness check."
    )
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    root = arguments.root.resolve(strict=True)
    if not (root / ".git").exists():
        raise RuntimeError(f"public release readiness requires a Git worktree: {root}")
    report = build_report(root)
    report_path = arguments.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "READY_FOR_PUBLIC_SOURCE_PREVIEW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
