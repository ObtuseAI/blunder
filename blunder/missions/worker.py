from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from blunder.chronos import ChronosContext, start_chronos_context
from blunder.constitutional_kernel import load_runtime_constitution
from blunder.execution_budget import optional_environment_int
from blunder.native_agent import run
from blunder.missions.branches import atomic_write_json, utc_now
from blunder.resource_accounting import build_resource_charge, sample_local_process


def failed_worker_charge(context: ChronosContext) -> dict[str, object]:
    return build_resource_charge(
        "mission-worker:failed",
        "native-mission",
        context,
        sample_local_process(context.mission_started_monotonic_ns),
        None,
        "mission-worker-unavailable-provider-usage",
        optional_environment_int("BLUNDER_MISSION_QUEUE_DEPTH"),
        optional_environment_int("BLUNDER_MISSION_QUEUE_CAPACITY"),
        datetime.now(timezone.utc),
    )


def execute(root: Path, task: str, provider_approved: bool, result_path: Path) -> int:
    context = start_chronos_context(load_runtime_constitution().policy_epoch, 0)
    try:
        report = run(root, task, provider_approved)
        resource_charge = report.get("resourceCharge")
        if not isinstance(resource_charge, dict):
            raise TypeError("Native mission report is missing its ResourceCharge.")
        atomic_write_json(
            result_path,
            {
                "schema": "obtuse.blunder.mission-worker-result.v1",
                "ok": True,
                "finishedAt": utc_now(),
                "report": report,
                "resourceCharge": resource_charge,
            },
        )
        print(json.dumps({"status": "BLUNDER_MISSION_COMPLETED", "result": str(result_path)}))
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        atomic_write_json(
            result_path,
            {
                "schema": "obtuse.blunder.mission-worker-result.v1",
                "ok": False,
                "finishedAt": utc_now(),
                "error": str(error),
                "resourceCharge": failed_worker_charge(context),
            },
        )
        print(json.dumps({"status": "BLUNDER_MISSION_FAILED", "error": str(error)}))
        return 1
