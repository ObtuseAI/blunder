from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from deployment.dumbmoney.migration import (  # noqa: E402
    MANIFEST_NAME,
    build_binary_review_template,
    plan_dopey_export,
    validate_export_package,
    write_export_package,
    write_quarantine_import,
)
from deployment.dumbmoney.common import load_json, write_new_json  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Plan, export, validate, or quarantine-import Dopey data. "
            "Dry-run is the default and no command can contact Robinhood."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export")
    export.add_argument("--source", type=Path, required=True)
    export.add_argument("--destination", type=Path)
    export.add_argument(
        "--write",
        action="store_true",
        help="materialize a new package; without this flag only a plan is printed",
    )
    export.add_argument(
        "--encrypted-transfer-ack",
        action="store_true",
        help=(
            "acknowledge the destination is protected by BitLocker, EFS, or an "
            "equivalent operator-controlled encrypted transport"
        ),
    )

    validate = subparsers.add_parser("validate")
    validate.add_argument("--package", type=Path, required=True)
    validate.add_argument("--binary-review", type=Path)

    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--package", type=Path, required=True)
    import_parser.add_argument("--target", type=Path)
    import_parser.add_argument("--binary-review", type=Path)
    import_parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "copy validated files to target/quarantine/<migration-id>; no active "
            "state is modified"
        ),
    )

    review = subparsers.add_parser("review-template")
    review.add_argument("--package", type=Path, required=True)
    review.add_argument(
        "--write-plan",
        type=Path,
        help="write a new unsigned review template without overwriting evidence",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        if args.command == "export":
            manifest = plan_dopey_export(args.source.resolve())
            result: dict[str, object] = {
                "operation": "EXPORT",
                "dry_run": not args.write,
                "broker_contacted": False,
                "manifest": manifest,
            }
            if args.write:
                if args.destination is None:
                    raise ValueError("--destination is required with --write")
                if not args.encrypted_transfer_ack:
                    raise ValueError(
                        "--encrypted-transfer-ack is required before materializing "
                        "a portable migration package"
                    )
                package = write_export_package(
                    args.source.resolve(),
                    args.destination.resolve(),
                    manifest,
                )
                result["package_root"] = str(package)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0

        if args.command == "review-template":
            manifest = load_json(args.package.resolve() / MANIFEST_NAME)
            template = build_binary_review_template(manifest)
            if args.write_plan:
                write_new_json(args.write_plan.resolve(), template)
            print(
                json.dumps(
                    {
                        "operation": "BINARY_REVIEW_TEMPLATE",
                        "status": "REVIEW_REQUIRED",
                        "dry_run": args.write_plan is None,
                        "template": template,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 2

        binary_review = (
            load_json(args.binary_review.resolve())
            if args.binary_review is not None
            else None
        )
        validation = validate_export_package(
            args.package.resolve(),
            binary_review_attestation=binary_review,
        )
        if args.command == "validate" or not args.write:
            result = validation.as_dict()
            result["dry_run"] = True
            result["broker_contacted"] = False
            print(json.dumps(result, indent=2, sort_keys=True))
            return {"PASS": 0, "FAIL": 1, "PARTIAL": 2}[validation.status]

        if args.target is None:
            raise ValueError("--target is required with --write")
        quarantine, receipt = write_quarantine_import(
            args.package.resolve(),
            args.target.resolve(),
            binary_review_attestation=binary_review,
        )
        print(
            json.dumps(
                {
                    "operation": "IMPORT",
                    "dry_run": False,
                    "status": "QUARANTINED",
                    "broker_contacted": False,
                    "quarantine_root": str(quarantine),
                    "receipt": receipt,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "broker_contacted": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
