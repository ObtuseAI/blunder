from __future__ import annotations

from pathlib import Path
import ast
import json
import re

from blunder.inflow.models import BlunderInflowRecord


def extract_symbols_from_text(text: str, suffix: str) -> list[dict[str, str]]:
    symbols: list[dict[str, str]] = []
    if suffix == ".py":
        try:
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    symbols.append({"kind": "function", "name": node.name})
                if isinstance(node, ast.ClassDef):
                    symbols.append({"kind": "class", "name": node.name})
        except SyntaxError:
            symbols.append({"kind": "parse_error", "name": "PYTHON_SYNTAX_ERROR"})
    for pattern, kind in [(r"function\s+([A-Za-z0-9_-]+)", "powershell_function"), (r"export\s+(?:function|const)\s+([A-Za-z0-9_]+)", "typescript_export")]:
        for match in re.finditer(pattern, text):
            symbols.append({"kind": kind, "name": match.group(1)})
    return symbols


def attach_code_symbols(artifact_root: Path, record: BlunderInflowRecord, mutate: bool) -> BlunderInflowRecord:
    if not record["raw_artifact_path"]:
        return record
    raw_path = Path(record["raw_artifact_path"])
    suffix = raw_path.suffix.lower()
    if suffix not in {".py", ".ps1", ".ts", ".tsx", ".js"}:
        return record
    symbols = extract_symbols_from_text(raw_path.read_text(encoding="utf-8", errors="replace"), suffix)
    if mutate:
        output_dir = artifact_root / "code_symbols"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"{record['record_id']}.json"
        output.write_text(json.dumps(symbols, indent=2, sort_keys=True), encoding="utf-8")
        record["code_symbols_path"] = str(output)
    return record
