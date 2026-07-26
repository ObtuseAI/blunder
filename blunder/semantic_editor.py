from __future__ import annotations

import ast
import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from blunder.constitutional_adapters import record_guarded_source_write_boundary
from blunder.repository_intelligence import source_paths


SYMBOL_PATTERN = re.compile(r"\b(?:class|def|function|interface|type)\s+([A-Za-z_][A-Za-z0-9_]*)")
MAX_EDITOR_BYTES = 512 * 1024


class Location(TypedDict):
    path: str
    name: str
    line: int
    column: int
    kind: str


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest().upper()


def python_symbols(path: str, content: str) -> tuple[list[Location], list[dict[str, object]]]:
    try:
        tree = ast.parse(content, filename=path)
    except SyntaxError as error:
        return [], [{
            "severity": "error",
            "message": error.msg,
            "line": int(error.lineno or 1),
            "column": int(error.offset or 1),
            "source": "python-parser",
        }]
    symbols: list[Location] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            symbols.append({"path": path, "line": node.lineno, "column": node.col_offset + 1, "kind": kind, "name": node.name})
    return sorted(symbols, key=lambda item: (item["line"], item["column"])), []


def text_symbols(path: str, content: str) -> list[Location]:
    symbols: list[Location] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        for match in SYMBOL_PATTERN.finditer(line):
            symbols.append({"path": path, "line": line_number, "column": match.start(1) + 1, "kind": "symbol", "name": match.group(1)})
    return symbols


def document_analysis(path: str, content: str) -> dict[str, object]:
    symbols: list[Location]
    diagnostics: list[dict[str, object]]
    if Path(path).suffix.lower() == ".py":
        symbols, diagnostics = python_symbols(path, content)
    else:
        symbols, diagnostics = text_symbols(path, content), []
    for line_number, line in enumerate(content.splitlines(), start=1):
        if line.endswith((" ", "\t")):
            diagnostics.append({
                "severity": "warning",
                "message": "Trailing whitespace",
                "line": line_number,
                "column": len(line),
                "source": "blunder-style",
            })
    return {
        "schema": "obtuse.blunder.semantic-document.v1",
        "path": path,
        "sha256": content_sha256(content),
        "lines": len(content.splitlines()),
        "symbols": symbols,
        "diagnostics": diagnostics,
    }


def symbol_definitions(root: Path, symbol: str, limit: int) -> list[Location]:
    normalized = symbol.strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized):
        raise ValueError("Definition lookup requires one exact symbol name.")
    results: list[Location] = []
    for path in source_paths(root.resolve()):
        relative = path.relative_to(root.resolve()).as_posix()
        content = path.read_text(encoding="utf-8", errors="replace")
        analysis = document_analysis(relative, content)
        for item in analysis["symbols"]:
            if isinstance(item, dict) and item.get("name") == normalized:
                results.append(item)
                if len(results) >= limit:
                    return results
    return results


def save_checkpoint_root(root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return root / "artifacts" / "obtuse" / "blunder-editor-checkpoints-v1" / stamp


def save_document(root: Path, path: Path, content: str, expected_sha256: str, confirmation: str) -> dict[str, object]:
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_EDITOR_BYTES:
        raise ValueError("Editor content exceeds the 512 KiB source limit.")
    before = path.read_text(encoding="utf-8", errors="replace")
    actual_sha256 = content_sha256(before)
    if actual_sha256 != expected_sha256.upper():
        raise ValueError("The file changed after it was opened; reload before saving.")
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError("Editor path escapes the Blunder workspace.")
    relative = resolved_path.relative_to(resolved_root).as_posix()
    expected_confirmation = f"SAVE {relative} {actual_sha256[:12]}"
    if confirmation.strip() != expected_confirmation:
        raise ValueError(f"Editor save requires exact confirmation: {expected_confirmation}")
    analysis = document_analysis(relative, content)
    errors = [item for item in analysis["diagnostics"] if isinstance(item, dict) and item.get("severity") == "error"]
    if errors:
        raise ValueError(f"Editor refused source with syntax diagnostics: {json.dumps(errors, ensure_ascii=False)}")
    constitutional_decision = record_guarded_source_write_boundary(
        root,
        "authenticated-shell-editor",
        "bounded-source-file",
        actual_sha256,
        datetime.now(timezone.utc),
    )
    checkpoint = save_checkpoint_root(root.resolve())
    snapshot = checkpoint / "snapshot" / relative
    snapshot.parent.mkdir(parents=True, exist_ok=False)
    snapshot.write_text(before, encoding="utf-8", newline="\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False, dir=path.parent) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)
    result = {
        "schema": "obtuse.blunder.editor-checkpoint.v1",
        "status": "SAVED_TO_REVIEW_TREE",
        "path": relative,
        "beforeSha256": actual_sha256,
        "afterSha256": content_sha256(content),
        "checkpoint": str(checkpoint),
        "committed": False,
        "pushed": False,
        "analysis": analysis,
        "constitutionalDecision": constitutional_decision,
    }
    (checkpoint / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
