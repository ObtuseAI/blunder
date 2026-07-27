from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from deployment.dumbmoney.dummy_epoch import (  # noqa: E402
    build_dummy_legacy_epoch_plan,
    write_dummy_epoch_plan,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect only Dummy ledger file metadata and emit a non-executable "
            "operational-epoch plan. This command has no checkpoint or broker mode."
        )
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path(r"C:\src\engine\dummy\runtime\autonomy\ledger.db"),
    )
    parser.add_argument(
        "--planned-epoch-root",
        type=Path,
        default=Path(
            r"C:\ProgramData\DumbMoney\dummy-kalshi\epochs\planned-operational"
        ),
    )
    parser.add_argument(
        "--write-plan",
        type=Path,
        help="write a new plan evidence file; existing files are never overwritten",
    )
    args = parser.parse_args()
    try:
        plan = build_dummy_legacy_epoch_plan(
            args.ledger.resolve(),
            planned_epoch_root=args.planned_epoch_root.resolve(),
        )
        if args.write_plan:
            write_dummy_epoch_plan(args.write_plan.resolve(), plan)
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "broker_contacted": False,
                    "checkpoint_requested": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
