from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol, Sequence

from blunder.cognition import normalized_terms


INTENT_GUARD_SCHEMA = "obtuse.blunder.intent-diff-firewall.v1"
EXCLUSIVE_PATTERN = re.compile(r"\b(?:only|solely|no other|nothing else)\b", re.IGNORECASE)
SCOPE_TOKEN_PATTERN = re.compile(r"\bP\d+\b|`([^`]+)`", re.IGNORECASE)
FORMAT_TERMS: frozenset[str] = frozenset({"format", "formatting", "punctuation", "whitespace", "style", "typography"})
TASK_STOP_TERMS: frozenset[str] = frozenset(
    {
        "add",
        "change",
        "changed",
        "changing",
        "explain",
        "file",
        "improve",
        "make",
        "number",
        "only",
        "other",
        "preserve",
        "source",
        "update",
    }
)


class ChangeLike(Protocol):
    path: str
    content: str
    reason: str


@dataclass(frozen=True)
class IntentHunk:
    path: str
    before_start: int
    before_end: int
    after_start: int
    after_end: int
    before: tuple[str, ...]
    after: tuple[str, ...]
    matched_terms: tuple[str, ...]
    scope_tokens: tuple[str, ...]
    cosmetic_only: bool
    aligned: bool


def semantic_text(value: str) -> str:
    normalized = value.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"[^a-z0-9]+", "", normalized.lower())


def scope_tokens(task: str) -> tuple[str, ...]:
    observed: list[str] = []
    for match in SCOPE_TOKEN_PATTERN.finditer(task):
        token = match.group(1) or match.group(0)
        normalized = token.replace("\\", "/").strip().lower()
        if normalized and normalized not in observed:
            observed.append(normalized)
    return tuple(observed)


def intent_terms(task: str) -> frozenset[str]:
    terms = normalized_terms(task).difference(TASK_STOP_TERMS)
    path_terms = frozenset(
        term
        for token in scope_tokens(task)
        if "/" in token or "." in token
        for term in normalized_terms(token)
    )
    return terms.difference(path_terms).difference({"blunder"})


def diff_hunks(path: str, before: str, after: str, task: str) -> tuple[IntentHunk, ...]:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    matcher = SequenceMatcher(None, before_lines, after_lines, autojunk=False)
    required_tokens = tuple(token for token in scope_tokens(task) if "/" not in token and "." not in token)
    terms = intent_terms(task)
    exclusive = EXCLUSIVE_PATTERN.search(task) is not None
    formatting_requested = bool(normalized_terms(task).intersection(FORMAT_TERMS))
    hunks: list[IntentHunk] = []
    operations: list[tuple[str, int, int, int, int]] = []
    for operation, before_start, before_end, after_start, after_end in matcher.get_opcodes():
        if operation == "equal":
            continue
        if operation == "replace" and before_end - before_start == after_end - after_start:
            operations.extend(
                (operation, before_start + offset, before_start + offset + 1, after_start + offset, after_start + offset + 1)
                for offset in range(before_end - before_start)
            )
        else:
            operations.append((operation, before_start, before_end, after_start, after_end))
    for operation, before_start, before_end, after_start, after_end in operations:
        removed = tuple(before_lines[before_start:before_end])
        added = tuple(after_lines[after_start:after_end])
        changed_text = "\n".join((*removed, *added))
        changed_terms = normalized_terms(changed_text)
        matched = tuple(sorted(terms.intersection(changed_terms)))
        present_tokens = tuple(token for token in required_tokens if token in changed_text.lower())
        cosmetic = bool(removed or added) and semantic_text("\n".join(removed)) == semantic_text("\n".join(added))
        token_aligned = not required_tokens or bool(present_tokens)
        semantic_aligned = bool(matched) or not terms
        aligned = token_aligned and semantic_aligned and (formatting_requested or not cosmetic)
        if not exclusive and not cosmetic and not required_tokens:
            aligned = True
        hunks.append(
            IntentHunk(
                path=path,
                before_start=before_start + 1,
                before_end=before_end,
                after_start=after_start + 1,
                after_end=after_end,
                before=removed,
                after=added,
                matched_terms=matched,
                scope_tokens=present_tokens,
                cosmetic_only=cosmetic,
                aligned=aligned,
            )
        )
    return tuple(hunks)


def intent_diff_firewall(root: Path, task: str, changes: Sequence[ChangeLike]) -> dict[str, object]:
    hunks: list[IntentHunk] = []
    findings: list[dict[str, object]] = []
    for change in changes:
        target = root / change.path
        before = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        observed = diff_hunks(change.path, before, change.content, task)
        hunks.extend(observed)
        for hunk in observed:
            if hunk.aligned:
                continue
            findings.append(
                {
                    "severity": "high",
                    "claim": "Changed hunk is not aligned with the operator's requested semantic scope.",
                    "evidencePath": hunk.path,
                    "afterLine": hunk.after_start,
                    "cosmeticOnly": hunk.cosmetic_only,
                    "matchedTerms": list(hunk.matched_terms),
                    "scopeTokens": list(hunk.scope_tokens),
                }
            )
    if not hunks:
        findings.append(
            {
                "severity": "high",
                "claim": "Proposal contains no observable line-level change.",
                "evidencePath": "proposal",
            }
        )
    return {
        "schema": INTENT_GUARD_SCHEMA,
        "verdict": "APPROVE" if not findings else "REJECT",
        "exclusive": EXCLUSIVE_PATTERN.search(task) is not None,
        "intentTerms": sorted(intent_terms(task)),
        "scopeTokens": list(scope_tokens(task)),
        "hunks": [asdict(hunk) for hunk in hunks],
        "findings": findings,
        "deterministic": True,
    }


def require_intent_approval(result: dict[str, object]) -> None:
    if result.get("verdict") != "APPROVE":
        raise ValueError(f"Intent-diff firewall rejected the proposal: {result.get('findings')}")
