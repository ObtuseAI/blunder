from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from blunder.ccos_canary import (
    ACTIVATION_CANARY_CONFIRMATION,
    ROLLBACK_CANARY_CONFIRMATION,
    run_operational_canary,
)
from blunder.ccos_runtime import (
    RECOVERY_CONFIRMATION,
    append_runtime_record,
    inspect_runtime_recovery,
    load_runtime_config,
    load_runtime_payload,
    recover_last_known_good,
    retention_report,
    runtime_records,
    runtime_state,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Operate the durable authority-closed Blunder CCOS evidence plane.")
    parser.add_argument("action", choices=("state", "records", "append", "canary", "retention", "recovery-inspect", "recover"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--kind")
    parser.add_argument("--input")
    parser.add_argument("--confirmation")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=100)
    arguments = parser.parse_args()
    root = Path(arguments.root).resolve()
    config = load_runtime_config(root, root / "configs" / "obtuse" / "blunder-ccos-runtime.json")
    now = datetime.now(timezone.utc)
    if arguments.action == "state":
        result = runtime_state(config)
    elif arguments.action == "records":
        if arguments.offset < 0 or arguments.limit < 1 or arguments.limit > 1000:
            raise ValueError("CCOS records require a non-negative offset and a limit between 1 and 1000.")
        kinds = () if arguments.kind is None else (arguments.kind,)
        records = runtime_records(config, kinds)
        page = records[arguments.offset : arguments.offset + arguments.limit]
        result = {
            "schema": "obtuse.blunder.ccos-runtime-record-list.v1",
            "count": len(page),
            "total": len(records),
            "offset": arguments.offset,
            "limit": arguments.limit,
            "records": list(page),
            "authorityGranted": False,
        }
    elif arguments.action == "append":
        if arguments.kind is None or arguments.input is None:
            raise ValueError("CCOS append requires --kind and --input.")
        result = append_runtime_record(config, arguments.kind, load_runtime_payload(Path(arguments.input)), now)
    elif arguments.action == "canary":
        result = run_operational_canary(
            config,
            now,
            ACTIVATION_CANARY_CONFIRMATION,
            ROLLBACK_CANARY_CONFIRMATION,
        )
    elif arguments.action == "retention":
        result = retention_report(config, now)
    elif arguments.action == "recovery-inspect":
        result = inspect_runtime_recovery(config, now)
    else:
        if arguments.confirmation != RECOVERY_CONFIRMATION:
            raise PermissionError(f"CCOS recovery requires --confirmation {RECOVERY_CONFIRMATION}")
        result = recover_last_known_good(config, arguments.confirmation)
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
