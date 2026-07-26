from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blunder.ai_architect import compile_ai_architecture
from blunder.automation_fabric import compile_automation
from blunder.creative_direction import compile_creative_brief
from blunder.open_tool_foundry import compile_tool_contract
from blunder.prompt_program import compile_prompt_program
from blunder.trace_creativity_lab import archive_creative_candidates, evaluate_trace


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Operate the first-party Blunder Creation Forge.")
    result.add_argument("action", choices=("creative", "ai-system", "automation", "tool", "prompt", "trace", "archive"))
    result.add_argument("--input", required=True)
    result.add_argument("--report", required=True)
    return result


def read_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Creation Forge input must be a JSON object: {path}")
    return value


def build_report(action: str, value: Mapping[str, object]) -> dict[str, object]:
    operations = {
        "creative": compile_creative_brief,
        "ai-system": compile_ai_architecture,
        "automation": compile_automation,
        "tool": compile_tool_contract,
        "prompt": compile_prompt_program,
        "trace": evaluate_trace,
    }
    if action == "archive":
        raw_candidates = value.get("candidates")
        if not isinstance(raw_candidates, list) or any(not isinstance(item, dict) for item in raw_candidates):
            raise ValueError("Creation Forge archive candidates must be an object list.")
        return archive_creative_candidates(tuple(item for item in raw_candidates if isinstance(item, dict)))
    return operations[action](value)


def write_report(path: Path, report: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(report), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    arguments = parser().parse_args()
    report = build_report(arguments.action, read_object(Path(arguments.input).resolve()))
    write_report(Path(arguments.report).resolve(), report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
