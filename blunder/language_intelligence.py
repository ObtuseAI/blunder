from __future__ import annotations

import ast
import keyword
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Sequence

from blunder.repository_intelligence import source_paths


LANGUAGE_SCHEMA = "obtuse.blunder.language-intelligence.v1"
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class Occurrence:
    path: str
    line: int
    column: int
    end_column: int
    kind: str


def python_tree(path: str, content: str) -> ast.AST:
    try:
        return ast.parse(content, filename=path)
    except SyntaxError as error:
        raise ValueError(f"Cannot analyze invalid Python source {path}: {error}") from error


def name_occurrences(path: str, content: str, symbol: str) -> tuple[Occurrence, ...]:
    tree = python_tree(path, content)
    observed: list[Occurrence] = []
    for node in ast.walk(tree):
        name: str | None = None
        kind = "reference"
        column = int(getattr(node, "col_offset", 0)) + 1
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
            kind = "definition"
        elif isinstance(node, ast.Name):
            name = node.id
            kind = "write" if isinstance(node.ctx, ast.Store) else "reference"
        elif isinstance(node, ast.Attribute):
            name = node.attr
            column = int(getattr(node, "end_col_offset", column)) - len(node.attr) + 1
        elif isinstance(node, ast.arg):
            name = node.arg
            kind = "parameter"
        if name != symbol:
            continue
        observed.append(
            Occurrence(
                path=path,
                line=int(getattr(node, "lineno", 1)),
                column=column,
                end_column=column + len(symbol),
                kind=kind,
            )
        )
    return tuple(sorted(observed, key=lambda item: (item.path, item.line, item.column, item.kind)))


def python_sources(root: Path) -> Iterable[tuple[str, str]]:
    resolved = root.resolve()
    for path in source_paths(resolved):
        if path.suffix.lower() != ".py":
            continue
        yield path.relative_to(resolved).as_posix(), path.read_text(encoding="utf-8", errors="replace")


def references(root: Path, symbol: str, limit: int) -> dict[str, object]:
    if IDENTIFIER.fullmatch(symbol) is None:
        raise ValueError("Reference lookup requires one valid identifier.")
    observed: list[Occurrence] = []
    parse_failures: list[str] = []
    for path, content in python_sources(root):
        try:
            observed.extend(name_occurrences(path, content, symbol))
        except ValueError:
            parse_failures.append(path)
        if len(observed) >= limit:
            break
    bounded = tuple(sorted(observed, key=lambda item: (item.path, item.line, item.column))[:limit])
    return {
        "schema": LANGUAGE_SCHEMA,
        "operation": "references",
        "symbol": symbol,
        "occurrences": [item.__dict__ for item in bounded],
        "count": len(bounded),
        "truncated": len(observed) > limit,
        "parseFailures": parse_failures,
    }


def completions(root: Path, prefix: str, limit: int) -> dict[str, object]:
    normalized = prefix.strip()
    if normalized and IDENTIFIER.fullmatch(normalized) is None:
        raise ValueError("Completion prefix must be an identifier fragment.")
    sources: dict[str, set[str]] = {}
    parse_failures: list[str] = []
    for path, content in python_sources(root):
        try:
            tree = ast.parse(content, filename=path)
        except SyntaxError:
            parse_failures.append(path)
            continue
        for node in ast.walk(tree):
            symbol = (
                node.name
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                else node.id
                if isinstance(node, ast.Name)
                else node.arg
                if isinstance(node, ast.arg)
                else None
            )
            if symbol and IDENTIFIER.fullmatch(symbol):
                sources.setdefault(symbol, set()).add(path)
    for word in keyword.kwlist:
        sources.setdefault(word, set()).add("python-keyword")
    ranked = sorted(
        (symbol for symbol in sources if symbol.lower().startswith(normalized.lower())),
        key=lambda symbol: (symbol.lower() != normalized.lower(), len(symbol), symbol.lower()),
    )[:limit]
    return {
        "schema": LANGUAGE_SCHEMA,
        "operation": "completions",
        "prefix": normalized,
        "items": [{"label": symbol, "sources": sorted(sources[symbol])[:5]} for symbol in ranked],
        "count": len(ranked),
        "parseFailures": parse_failures,
    }


def rename_plan(root: Path, symbol: str, replacement: str, limit: int) -> dict[str, object]:
    if IDENTIFIER.fullmatch(symbol) is None or IDENTIFIER.fullmatch(replacement) is None:
        raise ValueError("Rename requires valid source and replacement identifiers.")
    if symbol == replacement:
        raise ValueError("Rename source and replacement must differ.")
    source_references = references(root, symbol, limit)
    replacement_references = references(root, replacement, limit)
    occurrences = source_references["occurrences"]
    conflicts = replacement_references["occurrences"]
    return {
        "schema": LANGUAGE_SCHEMA,
        "operation": "rename-preview",
        "symbol": symbol,
        "replacement": replacement,
        "edits": [
            {
                "path": item["path"],
                "line": item["line"],
                "column": item["column"],
                "endColumn": item["end_column"],
                "replacement": replacement,
                "kind": item["kind"],
            }
            for item in occurrences
            if isinstance(item, dict)
        ],
        "editCount": len(occurrences),
        "conflicts": conflicts,
        "safeToApplyAutomatically": bool(occurrences) and not conflicts,
        "applied": False,
    }


def source_diagnostics(path: str, content: str) -> list[dict[str, object]]:
    diagnostics: list[dict[str, object]] = []
    try:
        tree = ast.parse(content, filename=path)
    except SyntaxError as error:
        return [
            {
                "path": path,
                "severity": "error",
                "message": error.msg,
                "line": int(error.lineno or 1),
                "column": int(error.offset or 1),
                "code": "python-syntax",
            }
        ]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is None:
            diagnostics.append(
                {
                    "path": path,
                    "severity": "information",
                    "message": f"Function {node.name} has no return annotation.",
                    "line": node.lineno,
                    "column": node.col_offset + 1,
                    "code": "missing-return-annotation",
                }
            )
    for line_number, line in enumerate(content.splitlines(), start=1):
        if line.endswith((" ", "\t")):
            diagnostics.append(
                {
                    "path": path,
                    "severity": "warning",
                    "message": "Trailing whitespace.",
                    "line": line_number,
                    "column": len(line),
                    "code": "trailing-whitespace",
                }
            )
    return diagnostics


def workspace_diagnostics(root: Path, limit: int) -> dict[str, object]:
    diagnostics: list[dict[str, object]] = []
    files = 0
    for path, content in python_sources(root):
        files += 1
        diagnostics.extend(source_diagnostics(path, content))
        if len(diagnostics) >= limit:
            break
    bounded = diagnostics[:limit]
    return {
        "schema": LANGUAGE_SCHEMA,
        "operation": "workspace-diagnostics",
        "filesAnalyzed": files,
        "diagnostics": bounded,
        "counts": {
            severity: sum(1 for item in bounded if item["severity"] == severity)
            for severity in ("error", "warning", "information")
        },
        "truncated": len(diagnostics) > limit,
    }


def top_level_symbols(content: str) -> frozenset[str]:
    tree = ast.parse(content)
    return frozenset(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )


def structural_diff(path: str, before: str, after: str) -> dict[str, object]:
    before_symbols = top_level_symbols(before) if Path(path).suffix.lower() == ".py" else frozenset()
    after_symbols = top_level_symbols(after) if Path(path).suffix.lower() == ".py" else frozenset()
    matcher = SequenceMatcher(None, before.splitlines(), after.splitlines(), autojunk=False)
    line_changes = [
        {
            "operation": operation,
            "before": [before_start + 1, before_end],
            "after": [after_start + 1, after_end],
        }
        for operation, before_start, before_end, after_start, after_end in matcher.get_opcodes()
        if operation != "equal"
    ]
    return {
        "schema": LANGUAGE_SCHEMA,
        "operation": "structural-diff",
        "path": path,
        "symbolsAdded": sorted(after_symbols.difference(before_symbols)),
        "symbolsRemoved": sorted(before_symbols.difference(after_symbols)),
        "symbolsPreserved": sorted(before_symbols.intersection(after_symbols)),
        "lineChanges": line_changes,
        "changed": before != after,
    }
