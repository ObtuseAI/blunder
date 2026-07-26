from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one first-party isolated Blunder mission worker.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--provider-approved", action="store_true")
    parser.add_argument("--identity-token", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from blunder.missions.worker import execute

    return execute(root, str(args.task), bool(args.provider_approved), Path(args.result).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
