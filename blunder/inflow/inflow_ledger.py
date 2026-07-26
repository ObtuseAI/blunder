from __future__ import annotations

from pathlib import Path
from typing import Any
import json


def reset_artifact_files(artifact_root: Path, file_names: list[str]) -> None:
    artifact_root.mkdir(parents=True, exist_ok=True)
    for file_name in file_names:
        path = artifact_root / file_name
        if path.exists():
            path.unlink()
        path.touch()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
