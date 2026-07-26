from __future__ import annotations

import ast
from collections import Counter
import json
import re
import tomllib
from html.parser import HTMLParser
from pathlib import Path
from typing import Mapping, Protocol, Sequence, TypedDict


PLACEHOLDER_PATTERN = re.compile(r"(?i)\b(?:todo|fixme|tbd|placeholder|implementation omitted|rest of (?:file|code))\b")
UNPROVEN_TEST_CLAIM = re.compile(r"(?i)\b(?:all\s+)?\d+(?:/\d+)?\s+(?:tests?|checks?)\s+(?:pass|passed|passing)\b")
PATH_REFERENCE = re.compile(r"`((?:blunder|tests|scripts|docs)/[^`\r\n]+|(?:README|SECURITY)\.md|pyproject\.toml)`")


class ChangeLike(Protocol):
    path: str
    content: str
    reason: str


class CourtResult(TypedDict):
    schema: str
    verdict: str
    phase: str
    checks: list[dict[str, object]]
    findings: list[dict[str, str]]
    deterministic: bool


class StrictHtmlParser(HTMLParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def syntax_error(path: str, content: str) -> str | None:
    suffix = Path(path).suffix.lower()
    try:
        if suffix == ".py":
            ast.parse(content, filename=path)
        elif suffix == ".json":
            json.loads(content)
        elif suffix == ".toml":
            tomllib.loads(content)
        elif suffix == ".html":
            parser = StrictHtmlParser()
            parser.feed(content)
            parser.close()
    except (SyntaxError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
        return str(error)
    return None


def add_check(checks: list[dict[str, object]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def newly_asserted_test_claim(current: str, proposed: str) -> str | None:
    current_claims = Counter(match.group(0).casefold() for match in UNPROVEN_TEST_CLAIM.finditer(current))
    for match in UNPROVEN_TEST_CLAIM.finditer(proposed):
        normalized = match.group(0).casefold()
        if current_claims[normalized] > 0:
            current_claims[normalized] -= 1
            continue
        return match.group(0)
    return None


def verification_court(
    root: Path,
    changes: Sequence[ChangeLike],
    selected_targets: Sequence[str],
    validation: Mapping[str, object] | None,
    phase: str,
) -> CourtResult:
    targets = {value.replace("\\", "/").strip("/").lower() for value in selected_targets}
    checks: list[dict[str, object]] = []
    findings: list[dict[str, str]] = []
    changed_paths = [change.path.replace("\\", "/").strip("/") for change in changes]
    escaped = sorted(path for path in changed_paths if path.lower() not in targets)
    add_check(checks, "selected-target-confinement", not escaped, "bounded" if not escaped else ", ".join(escaped))
    if escaped:
        findings.append({"severity": "critical", "claim": "Change escaped the selected plan.", "evidencePath": escaped[0]})
    for change in changes:
        current = root / change.path
        unchanged = current.is_file() and current.read_text(encoding="utf-8", errors="replace") == change.content
        material_passed = unchanged if phase == "postflight" else not unchanged
        material_detail = "applied content matches" if phase == "postflight" and unchanged else "content changes" if not unchanged else "no-op replacement"
        add_check(checks, f"material-change:{change.path}", material_passed, material_detail)
        if not material_passed:
            claim = "Applied source differs from the approved proposal." if phase == "postflight" else "Proposal contains a no-op replacement."
            findings.append({"severity": "high", "claim": claim, "evidencePath": change.path})
        syntax = syntax_error(change.path, change.content)
        add_check(checks, f"syntax:{change.path}", syntax is None, syntax or "parsed")
        if syntax is not None:
            findings.append({"severity": "critical", "claim": f"Source does not parse: {syntax}", "evidencePath": change.path})
        placeholder = PLACEHOLDER_PATTERN.search(change.content)
        add_check(checks, f"complete-content:{change.path}", placeholder is None, placeholder.group(0) if placeholder else "complete")
        if placeholder is not None:
            findings.append({"severity": "high", "claim": "Source contains an implementation placeholder.", "evidencePath": change.path})
        if Path(change.path).suffix.lower() == ".md":
            current_content = current.read_text(encoding="utf-8", errors="replace") if current.is_file() else ""
            unproven = newly_asserted_test_claim(current_content, change.content) if validation is None else None
            add_check(checks, f"measured-claims:{change.path}", unproven is None, unproven or "grounded")
            if unproven is not None:
                findings.append({"severity": "high", "claim": "Documentation asserts an unmeasured test result.", "evidencePath": change.path})
            missing = [reference for reference in PATH_REFERENCE.findall(change.content) if not (root / reference).exists() and reference not in changed_paths]
            add_check(checks, f"path-claims:{change.path}", not missing, ", ".join(missing) if missing else "resolved")
            if missing:
                findings.append({"severity": "high", "claim": "Documentation references a missing path.", "evidencePath": missing[0]})
    if validation is not None:
        tests_passed = validation.get("testsPassed") is True
        add_check(checks, "executable-acceptance", tests_passed, "tests passed" if tests_passed else "tests did not pass")
        if not tests_passed:
            findings.append({"severity": "critical", "claim": "Executable acceptance suite failed.", "evidencePath": "validation.testOutput"})
    verdict = "APPROVE" if all(bool(check["passed"]) for check in checks) else "REJECT"
    return {
        "schema": "obtuse.blunder.verification-court.v1",
        "verdict": verdict,
        "phase": phase,
        "checks": checks,
        "findings": findings,
        "deterministic": True,
    }


def require_approval(result: Mapping[str, object]) -> None:
    if result.get("verdict") != "APPROVE":
        raise ValueError(f"Deterministic verification court rejected the proposal: {json.dumps(result.get('findings'), ensure_ascii=False)}")
