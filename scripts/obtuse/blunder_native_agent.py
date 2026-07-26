from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one explicitly approved native Blunder task.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--provider-approved", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from blunder.native_agent import run

    try:
        result = run(root, str(args.task), bool(args.provider_approved))
    except (OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"status": "BLUNDER_NATIVE_TASK_FAILED", "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
