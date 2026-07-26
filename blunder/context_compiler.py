from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Mapping, Sequence, TypedDict

from blunder.cognition import normalized_terms
from blunder.repository_intelligence import build_repository_index, query_repository, source_paths


CONTEXT_SCHEMA = "obtuse.blunder.semantic-context.v1"
CONTEXT_WINDOW_LINES = 18


class ContextResult(TypedDict):
    sources: dict[str, str]
    manifest: dict[str, object]


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def line_ranges_for_python(text: str, terms: frozenset[str]) -> tuple[tuple[int, int], ...]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ()
    ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        identity = " ".join((node.name, ast.get_docstring(node) or "")).lower()
        if any(term in identity for term in terms):
            ranges.append((max(1, node.lineno - 3), int(getattr(node, "end_lineno", node.lineno)) + 3))
    return tuple(ranges)


def line_ranges_for_terms(lines: Sequence[str], terms: frozenset[str]) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    for line_number, line in enumerate(lines, start=1):
        lowered = line.lower()
        if any(term in lowered for term in terms):
            ranges.append((max(1, line_number - CONTEXT_WINDOW_LINES), line_number + CONTEXT_WINDOW_LINES))
    return tuple(ranges)


def merge_ranges(ranges: Iterable[tuple[int, int]], maximum_line: int) -> tuple[tuple[int, int], ...]:
    bounded = sorted((max(1, start), min(maximum_line, end)) for start, end in ranges if start <= maximum_line)
    merged: list[tuple[int, int]] = []
    for start, end in bounded:
        if merged and start <= merged[-1][1] + 2:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return tuple(merged)


def semantic_slice(text: str, terms: frozenset[str], maximum_chars: int) -> tuple[str, tuple[tuple[int, int], ...]]:
    lines = text.splitlines()
    if len(text) <= maximum_chars:
        return text, ((1, max(1, len(lines))),)
    candidates = (*line_ranges_for_python(text, terms), *line_ranges_for_terms(lines, terms))
    ranges = merge_ranges(candidates, len(lines))
    if not ranges:
        ranges = ((1, min(len(lines), 80)),)
    blocks: list[str] = []
    accepted: list[tuple[int, int]] = []
    consumed = 0
    for start, end in ranges:
        body = "\n".join(lines[start - 1 : end])
        block = f"[lines {start}-{end}]\n{body}\n"
        if consumed + len(block) > maximum_chars:
            remaining = maximum_chars - consumed
            if remaining > 160:
                blocks.append(block[:remaining] + "\n[semantic range truncated]\n")
                accepted.append((start, end))
            break
        blocks.append(block)
        accepted.append((start, end))
        consumed += len(block)
    return "\n".join(blocks), tuple(accepted)


def context_cache_path(root: Path, cache_key: str) -> Path:
    configured = os.environ.get("BLUNDER_CONTEXT_CACHE_ROOT", "").strip()
    cache_root = Path(configured).resolve() if configured else root / "artifacts" / "obtuse" / "blunder-context-cache-v1"
    return cache_root / f"{cache_key}.json"


def load_context_cache(path: Path) -> ContextResult | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("schema") != CONTEXT_SCHEMA:
        return None
    sources = value.get("sources")
    manifest = value.get("manifest")
    if not isinstance(sources, dict) or not isinstance(manifest, dict):
        return None
    return {"sources": {str(key): str(item) for key, item in sources.items()}, "manifest": dict(manifest)}


def compile_context(root: Path, task: str, maximum_bytes: int, maximum_file_chars: int) -> ContextResult:
    resolved = root.resolve()
    index = build_repository_index(resolved, True)
    fingerprint = str(index.get("fingerprint") or "")
    cache_key = text_sha256(f"{fingerprint}\n{task}\n{maximum_bytes}\n{maximum_file_chars}")
    cache = context_cache_path(resolved, cache_key)
    cached = load_context_cache(cache)
    if cached is not None:
        cached["manifest"]["cacheHit"] = True
        return cached
    terms = normalized_terms(task).difference({"blunder", "change", "file", "source"})
    ranked = query_repository(index, task, 80)
    rank_by_path = {str(item.get("path") or ""): float(item.get("score") or 0.0) for item in ranked}
    paths = tuple(source_paths(resolved))
    content_by_path = {path: path.read_text(encoding="utf-8", errors="replace") for path in paths}
    def relevance(path: Path) -> float:
        relative = path.relative_to(resolved).as_posix().lower()
        lowered = content_by_path[path].lower()
        return rank_by_path.get(path.relative_to(resolved).as_posix(), 0.0) + sum(
            8.0 if term in relative else 3.0 if term in lowered else 0.0 for term in terms
        )
    candidates = sorted(paths, key=lambda path: (-relevance(path), path.as_posix()))
    sources: dict[str, str] = {}
    ranges_by_path: dict[str, list[list[int]]] = {}
    consumed = 0
    for path in candidates:
        relative = path.relative_to(resolved).as_posix()
        text = content_by_path[path]
        content, ranges = semantic_slice(text, terms, maximum_file_chars)
        block_bytes = len(f"\n--- {relative} ---\n{content}\n".encode("utf-8"))
        if consumed + block_bytes > maximum_bytes:
            continue
        sources[relative] = content
        ranges_by_path[relative] = [[start, end] for start, end in ranges]
        consumed += block_bytes
    manifest: dict[str, object] = {
        "schema": CONTEXT_SCHEMA,
        "cacheKey": cache_key,
        "cacheHit": False,
        "repositoryFingerprint": fingerprint,
        "sourceCount": len(sources),
        "bytes": consumed,
        "estimatedTokens": (consumed + 3) // 4,
        "ranges": ranges_by_path,
    }
    result: ContextResult = {"sources": sources, "manifest": manifest}
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"schema": CONTEXT_SCHEMA, **result}, indent=2) + "\n", encoding="utf-8")
    temporary.replace(cache)
    return result
