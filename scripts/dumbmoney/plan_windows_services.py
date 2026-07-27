from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from deployment.dumbmoney.common import write_new_json  # noqa: E402
from deployment.dumbmoney.service_plan import (  # noqa: E402
    build_windows_service_plan,
    load_services_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a review plan for the five DumbMoney Windows services. "
            "This command cannot install, start, stop, or modify a service."
        )
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPOSITORY_ROOT / "deployment" / "dumbmoney" / "services.v1.json",
    )
    parser.add_argument("--release-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--program-data-root", default=r"C:\ProgramData\DumbMoney")
    parser.add_argument(
        "--write-plan",
        type=Path,
        help="write a new plan evidence file; existing files are never overwritten",
    )
    args = parser.parse_args()
    try:
        manifest = load_services_manifest(args.manifest.resolve())
        plan = build_windows_service_plan(
            manifest,
            release_root=args.release_root.resolve(),
            program_data_root=args.program_data_root,
        )
        if args.write_plan:
            write_new_json(args.write_plan.resolve(), plan)
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"},
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0 if plan["validation_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
