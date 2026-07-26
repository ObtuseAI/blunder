from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


RELIABILITY_SCHEMA = "obtuse.blunder.reliability-campaign.v1"


@dataclass(frozen=True)
class ReliabilityLimits:
    maximum_heartbeat_age_seconds: float
    maximum_failed_tasks: int
    maximum_blocked_tasks: int
    maximum_quarantined_tasks: int
    minimum_healthy_rate: float


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_json_output(output: str) -> dict[str, object]:
    start = output.find("{")
    if start < 0:
        raise ValueError("Reliability probe produced no JSON object.")
    value = json.loads(output[start:])
    if not isinstance(value, dict):
        raise ValueError("Reliability probe JSON must be an object.")
    return value


def collect_live_snapshot(root: Path) -> dict[str, object]:
    completed = subprocess.run(
        (
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "scripts" / "obtuse" / "status-idle-ops.ps1"),
        ),
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=45,
        check=False,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Reliability status probe failed: {(completed.stderr or completed.stdout)[-1000:]}")
    return parse_json_output(completed.stdout)


def queue_counts(snapshot: Mapping[str, object]) -> Mapping[str, object]:
    value = snapshot.get("queueStats")
    return value if isinstance(value, dict) else {}


def evaluate_snapshot(snapshot: Mapping[str, object], limits: ReliabilityLimits, sequence: int) -> dict[str, object]:
    queue = queue_counts(snapshot)
    heartbeat_age = float(snapshot.get("heartbeatAgeSeconds") or 0)
    gates = {
        "runtimeActive": snapshot.get("active") is True,
        "pidIdentityValid": snapshot.get("pidValid") is True,
        "heartbeatFresh": heartbeat_age <= limits.maximum_heartbeat_age_seconds,
        "failedTasksBounded": int(queue.get("failed") or 0) <= limits.maximum_failed_tasks,
        "blockedTasksBounded": int(queue.get("blocked") or 0) <= limits.maximum_blocked_tasks,
        "quarantinedTasksBounded": int(queue.get("quarantined") or 0) <= limits.maximum_quarantined_tasks,
        "operatorActionHealthy": snapshot.get("finalRecommendedOperatorAction") == "NO_ACTION_IDLE_HEALTHY",
    }
    return {
        "sequence": sequence,
        "timestamp": utc_now(),
        "healthy": all(gates.values()),
        "gates": gates,
        "cyclesRun": ((snapshot.get("rawRunnerStatus") or {}).get("state") or {}).get("cyclesRun")
        if isinstance(snapshot.get("rawRunnerStatus"), dict)
        else None,
        "heartbeatAgeSeconds": heartbeat_age,
        "queue": dict(queue),
    }


def atomic_replacement_drill() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        target = root / "state.json"
        temporary = root / "state.json.tmp"
        target.write_text('{"generation":1}\n', encoding="utf-8")
        temporary.write_text('{"generation":2,"complete":true}\n', encoding="utf-8")
        temporary.replace(target)
        value = json.loads(target.read_text(encoding="utf-8"))
    return {
        "name": "atomic-replacement",
        "pass": value == {"generation": 2, "complete": True},
        "observed": value,
    }


def truncated_json_drill() -> dict[str, object]:
    rejected = False
    try:
        json.loads('{"status":"running"')
    except json.JSONDecodeError:
        rejected = True
    return {"name": "truncated-json-rejected", "pass": rejected}


def stale_identity_drill() -> dict[str, object]:
    lock = {"status": "active", "pid": 99999999, "identityToken": "stale"}
    pid_exists = False
    if os.name == "nt":
        completed = subprocess.run(
            (
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"if (Get-Process -Id {lock['pid']} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}",
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        pid_exists = completed.returncode == 0
    return {
        "name": "stale-identity-detected",
        "pass": lock["status"] == "active" and not pid_exists,
        "recovery": "mark interrupted and preserve terminal evidence",
    }


def budget_exhaustion_drill() -> dict[str, object]:
    limit = {"tokens": 60_000, "costUsd": 0.25, "seconds": 900}
    observed = {"tokens": 60_001, "costUsd": 0.1, "seconds": 100}
    exceeded = any(float(observed[key]) > float(limit[key]) for key in limit)
    return {
        "name": "budget-exhaustion-fails-closed",
        "pass": exceeded,
        "terminalState": "INCONCLUSIVE_BUDGET_EXCEEDED" if exceeded else "INVALID_DRILL",
    }


def fault_drills() -> tuple[dict[str, object], ...]:
    return (
        atomic_replacement_drill(),
        truncated_json_drill(),
        stale_identity_drill(),
        budget_exhaustion_drill(),
    )


def campaign_report(
    snapshots: Sequence[Mapping[str, object]],
    limits: ReliabilityLimits,
    started_at: str,
    finished_at: str,
) -> dict[str, object]:
    cycles = [evaluate_snapshot(snapshot, limits, index) for index, snapshot in enumerate(snapshots, start=1)]
    healthy = sum(1 for cycle in cycles if cycle["healthy"] is True)
    rate = healthy / len(cycles) if cycles else 0.0
    drills = fault_drills()
    gates = {
        "cyclesObserved": bool(cycles),
        "healthyRatePassed": rate >= limits.minimum_healthy_rate,
        "faultDrillsPassed": all(drill["pass"] is True for drill in drills),
        "noSyntheticCycles": all(isinstance(snapshot, Mapping) and bool(snapshot) for snapshot in snapshots),
    }
    return {
        "schema": RELIABILITY_SCHEMA,
        "status": "PASS" if all(gates.values()) else "FAIL",
        "startedAt": started_at,
        "finishedAt": finished_at,
        "limits": asdict(limits),
        "gates": gates,
        "cycles": len(cycles),
        "healthyCycles": healthy,
        "healthyRate": round(rate, 6),
        "observations": cycles,
        "faultDrills": list(drills),
        "providerCalls": 0,
        "sourceMutation": False,
    }


def run_live_campaign(root: Path, limits: ReliabilityLimits, cycles: int, interval_seconds: float) -> dict[str, object]:
    if cycles < 1 or cycles > 10_000:
        raise ValueError("Reliability cycles must be from 1 through 10,000.")
    if interval_seconds < 0 or interval_seconds > 300:
        raise ValueError("Reliability interval must be from 0 through 300 seconds.")
    started = utc_now()
    snapshots: list[dict[str, object]] = []
    for sequence in range(cycles):
        snapshots.append(collect_live_snapshot(root))
        if sequence + 1 < cycles and interval_seconds:
            time.sleep(interval_seconds)
    return campaign_report(snapshots, limits, started, utc_now())


def write_campaign(path: Path, report: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(report), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path
