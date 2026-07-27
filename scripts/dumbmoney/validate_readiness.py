from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from deployment.dumbmoney.common import load_json  # noqa: E402
from deployment.dumbmoney.readiness import (  # noqa: E402
    EXPECTED_SERVICES,
    make_ed25519_verifier,
    validate_readiness_descriptor,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a dynamic signed readiness descriptor; this command has no "
            "service-control or broker authority."
        )
    )
    parser.add_argument("descriptor", type=Path)
    parser.add_argument("--expected-service", choices=sorted(EXPECTED_SERVICES))
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--key-id")
    parser.add_argument("--require-parallel-contract", action="store_true")
    args = parser.parse_args()
    try:
        descriptor = load_json(args.descriptor.resolve())
        verifier = (
            make_ed25519_verifier(
                args.public_key.resolve(), expected_key_id=args.key_id
            )
            if args.public_key
            else None
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "subject": "readiness-descriptor",
                    "status": "FAIL",
                    "issues": [
                        {
                            "severity": "ERROR",
                            "code": "READINESS_INPUT_UNREADABLE",
                            "message": f"{type(exc).__name__}: {exc}",
                        }
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    report = validate_readiness_descriptor(
        descriptor,
        expected_service=args.expected_service,
        verifier=verifier,
        require_parallel_contract=args.require_parallel_contract,
        now_utc=datetime.now(timezone.utc),
    )
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return {"PASS": 0, "FAIL": 1, "PARTIAL": 2}[report.status]


if __name__ == "__main__":
    raise SystemExit(main())
