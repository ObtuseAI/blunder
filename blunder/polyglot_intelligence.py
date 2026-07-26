from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence


POLYGLOT_SCHEMA = "obtuse.blunder.polyglot-intelligence.v1"
BLOCKED_PARTS: frozenset[str] = frozenset({".git", "node_modules", "artifacts", "dist", "build", ".next", ".godot", "Library"})


@dataclass(frozen=True)
class LanguageSpec:
    language: str
    suffixes: tuple[str, ...]
    symbol_patterns: tuple[str, ...]
    import_patterns: tuple[str, ...]
    brace_delimited: bool


LANGUAGES: tuple[LanguageSpec, ...] = (
    LanguageSpec("typescript", (".ts", ".tsx"), (r"\b(?:class|interface|type|enum|function)\s+([A-Za-z_$][\w$]*)", r"\b(?:const|let)\s+([A-Za-z_$][\w$]*)\s*="), (r"\bfrom\s+[\"']([^\"']+)", r"\brequire\s*\(\s*[\"']([^\"']+)"), True),
    LanguageSpec("javascript", (".js", ".jsx", ".mjs", ".cjs"), (r"\b(?:class|function)\s+([A-Za-z_$][\w$]*)", r"\b(?:const|let)\s+([A-Za-z_$][\w$]*)\s*="), (r"\bfrom\s+[\"']([^\"']+)", r"\brequire\s*\(\s*[\"']([^\"']+)"), True),
    LanguageSpec("css", (".css", ".scss"), (r"(?:^|\})\s*([.#][A-Za-z_-][\w-]*)\s*\{", r"--([A-Za-z_-][\w-]*)\s*:"), (r"@import\s+[\"']([^\"']+)",), True),
    LanguageSpec("swift", (".swift",), (r"\b(?:class|struct|protocol|enum|func)\s+([A-Za-z_][\w]*)",), (r"\bimport\s+([A-Za-z_][\w.]*)",), True),
    LanguageSpec("kotlin", (".kt", ".kts"), (r"\b(?:class|interface|object|enum\s+class|fun)\s+([A-Za-z_][\w]*)",), (r"\bimport\s+([A-Za-z_][\w.]*)",), True),
    LanguageSpec("dart", (".dart",), (r"\b(?:class|enum|mixin|extension)\s+([A-Za-z_][\w]*)", r"\b(?:void|Future<[^>]+>|[A-Za-z_][\w<>?]*)\s+([A-Za-z_][\w]*)\s*\("), (r"\bimport\s+[\"']([^\"']+)",), True),
    LanguageSpec("csharp", (".cs",), (r"\b(?:class|interface|struct|enum|record)\s+([A-Za-z_][\w]*)",), (r"\busing\s+([A-Za-z_][\w.]*)",), True),
    LanguageSpec("gdscript", (".gd",), (r"\b(?:class_name|func)\s+([A-Za-z_][\w]*)",), (r"\b(?:extends|preload|load)\s*\(?[\"']([^\"']+)",), False),
    LanguageSpec("shader", (".glsl", ".hlsl", ".wgsl", ".shader"), (r"\b(?:fn|void|float\d?|half\d?)\s+([A-Za-z_][\w]*)\s*\(", r"\bstruct\s+([A-Za-z_][\w]*)"), (r"#include\s+[\"<]([^\">]+)",), True),
    LanguageSpec("markup", (".html", ".vue", ".svelte"), (r"\bid=[\"']([^\"']+)", r"\b(?:class|data-component)=[\"']([^\"']+)"), (r"\b(?:src|href)=[\"']([^\"']+)",), True),
)


def spec_for(path: Path) -> LanguageSpec | None:
    suffix = path.suffix.lower()
    return next((spec for spec in LANGUAGES if suffix in spec.suffixes), None)


def source_files(root: Path) -> tuple[Path, ...]:
    resolved = root.resolve()
    supported = frozenset(suffix for spec in LANGUAGES for suffix in spec.suffixes)
    return tuple(
        path
        for path in resolved.rglob("*")
        if path.is_file()
        and path.suffix.lower() in supported
        and path.stat().st_size <= 512 * 1024
        and not set(path.relative_to(resolved).parts).intersection(BLOCKED_PARTS)
    )


def pattern_values(patterns: Sequence[str], content: str) -> tuple[str, ...]:
    values: set[str] = set()
    for pattern in patterns:
        values.update(match.strip() for match in re.findall(pattern, content, re.MULTILINE) if match.strip())
    return tuple(sorted(values))


def delimiter_findings(content: str, brace_delimited: bool) -> list[dict[str, object]]:
    if not brace_delimited:
        return []
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[tuple[str, int]] = []
    quote: str | None = None
    escaped = False
    line = 1
    for character in content:
        if character == "\n":
            line += 1
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote:
            escaped = True
            continue
        if quote:
            if character == quote:
                quote = None
            continue
        if character in {"'", '"', "`"}:
            quote = character
        elif character in "([{":
            stack.append((character, line))
        elif character in ")]}":
            if not stack or stack[-1][0] != pairs[character]:
                return [{"severity": "error", "line": line, "code": "unmatched-closing-delimiter", "message": f"Unmatched {character}."}]
            stack.pop()
    findings = [
        {"severity": "error", "line": observed_line, "code": "unclosed-delimiter", "message": f"Unclosed {character}."}
        for character, observed_line in stack[-20:]
    ]
    if quote:
        findings.append({"severity": "error", "line": line, "code": "unclosed-string", "message": "Unclosed string literal."})
    return findings


def file_record(root: Path, path: Path) -> dict[str, object]:
    spec = spec_for(path)
    if spec is None:
        raise ValueError(f"Unsupported polyglot source: {path}")
    content = path.read_text(encoding="utf-8", errors="replace")
    diagnostics = delimiter_findings(content, spec.brace_delimited)
    diagnostics.extend(
        {
            "severity": "warning",
            "line": line,
            "code": "trailing-whitespace",
            "message": "Trailing whitespace.",
        }
        for line, value in enumerate(content.splitlines(), start=1)
        if value.endswith((" ", "\t"))
    )
    return {
        "path": path.relative_to(root.resolve()).as_posix(),
        "language": spec.language,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest().upper(),
        "bytes": path.stat().st_size,
        "symbols": list(pattern_values(spec.symbol_patterns, content)),
        "imports": list(pattern_values(spec.import_patterns, content)),
        "diagnostics": diagnostics,
        "analysisMode": "deterministic-lexical-plus-language-patterns",
    }


def polyglot_index(root: Path) -> dict[str, object]:
    resolved = root.resolve()
    records = [file_record(resolved, path) for path in source_files(resolved)]
    counts = {
        language: sum(1 for item in records if item["language"] == language)
        for language in sorted({str(item["language"]) for item in records})
    }
    diagnostics = [dict(item) | {"path": record["path"]} for record in records for item in record["diagnostics"]]
    return {
        "schema": POLYGLOT_SCHEMA,
        "status": "PASS" if not any(item["severity"] == "error" for item in diagnostics) else "FAIL_DIAGNOSTICS",
        "files": records,
        "fileCount": len(records),
        "languageCounts": counts,
        "symbolCount": sum(len(item["symbols"]) for item in records),
        "diagnostics": diagnostics,
        "supportedLanguages": [asdict(spec) | {"symbol_patterns": list(spec.symbol_patterns), "import_patterns": list(spec.import_patterns)} for spec in LANGUAGES],
        "sourceMutation": False,
        "providerCalls": 0,
    }


def polyglot_references(root: Path, symbol: str, limit: int) -> dict[str, object]:
    if re.fullmatch(r"[A-Za-z_$][\w$-]*", symbol) is None:
        raise ValueError("Polyglot references require one valid symbol.")
    pattern = re.compile(rf"(?<![\w$-]){re.escape(symbol)}(?![\w$-])")
    occurrences: list[dict[str, object]] = []
    for path in source_files(root):
        for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            occurrences.extend(
                {
                    "path": path.relative_to(root.resolve()).as_posix(),
                    "line": line_number,
                    "column": match.start() + 1,
                    "language": spec_for(path).language if spec_for(path) else "unknown",
                }
                for match in pattern.finditer(line)
            )
            if len(occurrences) >= limit:
                break
        if len(occurrences) >= limit:
            break
    return {
        "schema": POLYGLOT_SCHEMA,
        "operation": "references",
        "symbol": symbol,
        "occurrences": occurrences[:limit],
        "count": min(len(occurrences), limit),
        "truncated": len(occurrences) > limit,
    }
