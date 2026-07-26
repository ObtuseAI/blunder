from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from blunder.cognition import normalized_terms


INDEX_SCHEMA = "obtuse.blunder.repository-intelligence.v1"
SOURCE_SUFFIXES: frozenset[str] = frozenset(
    {
        ".cfg", ".cjs", ".cs", ".css", ".dart", ".gd", ".glsl", ".godot", ".gradle", ".hlsl", ".html",
        ".ini", ".js", ".json", ".jsx", ".kt", ".kts", ".md", ".mjs", ".properties",
        ".ps1", ".py", ".sass", ".scss", ".shader", ".svelte", ".svg", ".swift", ".toml",
        ".ts", ".tsx", ".vue", ".wgsl", ".xml", ".yaml", ".yml",
    }
)
BLOCKED_PARTS: frozenset[str] = frozenset(
    {".git", ".venv", ".venv-blunder-shell", "__pycache__", "artifacts", "dist", "node_modules"}
)
IMPORT_PATTERN = re.compile(r"(?:from\s+|require\s*\(|import\s+[^'\"]*?from\s+)[\"']([^\"']+)")
SYMBOL_PATTERN = re.compile(r"\b(?:class|def|function|interface|type)\s+([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class SourceRecord:
    path: str
    sha256: str
    bytes: int
    lines: int
    symbols: tuple[str, ...]
    imports: tuple[str, ...]
    test_file: bool
    git_change_count: int
    currently_changed: bool


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def source_paths(root: Path) -> Iterable[Path]:
    discovered: list[Path] = []
    for current, directories, filenames in os.walk(root):
        directories[:] = sorted(directory for directory in directories if directory not in BLOCKED_PARTS)
        current_path = Path(current)
        for filename in filenames:
            path = current_path / filename
            if path.suffix.lower() in SOURCE_SUFFIXES and path.stat().st_size <= 256 * 1024:
                discovered.append(path)
    yield from sorted(discovered)


def python_facts(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return (), ()
    symbols: list[str] = []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(node.name)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return tuple(sorted(set(symbols))), tuple(sorted(set(imports)))


def text_facts(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    symbols = tuple(sorted(set(SYMBOL_PATTERN.findall(text))))
    imports = tuple(sorted(set(IMPORT_PATTERN.findall(text))))
    return symbols, imports


def git_lines(root: Path, arguments: Sequence[str]) -> tuple[str, ...]:
    process = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    return tuple(line.strip().replace("\\", "/") for line in process.stdout.splitlines() if line.strip())


def git_evidence(root: Path) -> tuple[dict[str, int], frozenset[str]]:
    history: dict[str, int] = {}
    for relative in git_lines(root, ("log", "-n", "80", "--format=", "--name-only", "--")):
        history[relative] = history.get(relative, 0) + 1
    changed = frozenset(git_lines(root, ("status", "--short")))
    changed_paths = frozenset(line[3:] for line in changed if len(line) > 3)
    return history, changed_paths


def repository_fingerprint(paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        stat = path.stat()
        digest.update(str(path).encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
    return digest.hexdigest().upper()


def build_repository_index(root: Path, persist: bool) -> dict[str, object]:
    resolved = root.resolve()
    paths = tuple(source_paths(resolved))
    fingerprint = repository_fingerprint(paths)
    cache = resolved / "artifacts" / "obtuse" / "blunder-repository-intelligence-v1" / "index.json"
    if cache.is_file():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = None
        if isinstance(cached, dict) and cached.get("fingerprint") == fingerprint:
            return cached
    history, changed = git_evidence(resolved)
    records: list[SourceRecord] = []
    for path in paths:
        relative = path.relative_to(resolved).as_posix()
        content = path.read_text(encoding="utf-8", errors="replace")
        symbols, imports = python_facts(content) if path.suffix.lower() == ".py" else text_facts(content)
        records.append(
            SourceRecord(
                path=relative,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest().upper(),
                bytes=path.stat().st_size,
                lines=len(content.splitlines()),
                symbols=symbols,
                imports=imports,
                test_file=relative.startswith("tests/") or "/test" in relative.lower(),
                git_change_count=history.get(relative, 0),
                currently_changed=relative in changed,
            )
        )
    result: dict[str, object] = {
        "schema": INDEX_SCHEMA,
        "createdAt": utc_now(),
        "fingerprint": fingerprint,
        "records": [asdict(record) for record in records],
        "counts": {
            "files": len(records),
            "symbols": sum(len(record.symbols) for record in records),
            "imports": sum(len(record.imports) for record in records),
            "tests": sum(1 for record in records if record.test_file),
        },
    }
    if persist:
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        temporary.replace(cache)
    return result


def import_matches(import_name: str, path: str) -> bool:
    normalized = import_name.replace(".", "/").strip("/")
    stem = path.rsplit(".", 1)[0]
    return stem.endswith(normalized) or normalized.endswith(stem)


def query_repository(index: Mapping[str, object], task: str, limit: int) -> list[dict[str, object]]:
    terms = normalized_terms(task).difference({"blunder", "change", "file", "source"})
    raw_records = index.get("records")
    records = [item for item in raw_records if isinstance(item, dict)] if isinstance(raw_records, list) else []
    direct_scores: dict[str, float] = {}
    for record in records:
        path = str(record.get("path") or "")
        symbols = " ".join(str(item) for item in record.get("symbols", []))
        score = sum(8 for term in terms if term in path.lower())
        score += sum(5 for term in terms if term in symbols.lower())
        score += min(5, int(record.get("git_change_count") or 0)) * 0.25
        score += 2.0 if record.get("currently_changed") is True else 0.0
        direct_scores[path] = score
    expanded = dict(direct_scores)
    strong = {path for path, score in direct_scores.items() if score >= 5}
    for record in records:
        path = str(record.get("path") or "")
        imports = tuple(str(item) for item in record.get("imports", []))
        if any(any(import_matches(name, target) for name in imports) for target in strong):
            expanded[path] = expanded.get(path, 0.0) + 3.0
        if record.get("test_file") is True and any(term in path.lower() for term in terms):
            expanded[path] = expanded.get(path, 0.0) + 3.0
    ranked = sorted(records, key=lambda item: (-expanded.get(str(item.get("path") or ""), 0.0), str(item.get("path") or "")))
    return [dict(item) | {"score": round(expanded.get(str(item.get("path") or ""), 0.0), 3)} for item in ranked[:limit]]


def intelligence_public(index: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema": index.get("schema"),
        "createdAt": index.get("createdAt"),
        "fingerprint": index.get("fingerprint"),
        "counts": index.get("counts"),
    }
