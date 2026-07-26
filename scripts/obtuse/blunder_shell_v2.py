from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blunder.shell.server import serve
from blunder.shell.service import validate_task, workspace_state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the localhost-only Blunder Focus Shell.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--smoke", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    static_root = root / "blunder" / "shell" / "static"
    required = [static_root / "index.html", static_root / "app.js", static_root / "styles.css"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Blunder Shell assets are missing: {missing}")
    if args.smoke:
        validate_task("Inspect Blunder and improve one bounded source area.")
        state = workspace_state(root)
        print(
            json.dumps(
                {
                    "status": "BLUNDER_SHELL_SMOKE_PASS",
                    "version": 4,
                    "assets": len(required),
                    "workspaceSchema": state["schema"],
                    "localhostOnly": True,
                    "providerCallsAttempted": False,
                    "benchmarkLaunchAttempted": False,
                },
                indent=2,
            )
        )
        return 0
    if args.port < 1 or args.port > 65535:
        raise ValueError("Port must be between 1 and 65535.")
    serve(root, static_root, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
