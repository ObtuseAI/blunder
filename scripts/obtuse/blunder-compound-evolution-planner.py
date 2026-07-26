from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blunder.evolution.compound_accelerator import build_compound_plan


def read_jsonl(path: Path) -> list[Mapping[str, object]]:
    if not path.exists():
        return []
    rows: list[Mapping[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"invalid JSONL at {path} line {line_number}: {error}") from error
        if not isinstance(value, dict):
            raise RuntimeError(f"invalid JSONL object at {path} line {line_number}: expected object")
        rows.append(value)
    return rows


def write_json_atomic(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Blunder's native advisory compound-evolution plan.")
    parser.add_argument("--ledger-root", required=True)
    parser.add_argument("--plan-path", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ledger_root = Path(args.ledger_root).resolve()
    plan_path = Path(args.plan_path).resolve()
    patch_rows = read_jsonl(ledger_root / "self_coding_patch_ledger.jsonl")
    promotion_rows = read_jsonl(ledger_root / "promotion_ledger.jsonl")
    plan = build_compound_plan(patch_rows, promotion_rows)
    write_json_atomic(plan_path, plan)
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
