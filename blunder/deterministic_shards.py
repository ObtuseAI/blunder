from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Mapping, Sequence

from blunder.chronos import start_chronos_context
from blunder.constitutional_kernel import load_runtime_constitution
from blunder.resource_accounting import build_resource_charge
from blunder.validation_broker import BrokerTimeoutExpired, brokered_run


SHARD_SCHEMA = "obtuse.blunder.deterministic-validation-shard.v1"
TARGET_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
DURATION_PATTERN = re.compile(r"\b(?:in|elapsed[=:]?)\s+\d+(?:\.\d+)?\s*(?:ms|s|seconds?)\b", re.IGNORECASE)
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-(?:or-v1|proj)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{24,}\b"),
    re.compile(r"(?i)\b(?:api[_ -]?key|token|secret|password)\s*[=:]\s*\S+"),
)
ENVIRONMENT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROGRAMDATA",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
)


@dataclass(frozen=True)
class ShardSpec:
    shard_id: str
    command_kind: str
    targets: tuple[str, ...]
    repeat_count: int
    timeout_seconds: int
    memory_bytes: int


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def require_integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise ValueError(f"{label} must be an integer from {minimum} through {maximum}.")
    return value


def parse_shard_spec(value: Mapping[str, object]) -> ShardSpec:
    shard_id = str(value.get("id") or "").strip()
    if TARGET_PATTERN.fullmatch(shard_id) is None:
        raise ValueError("Validation shard id must contain only letters, numbers, dots, dashes, or underscores.")
    command_kind = str(value.get("commandKind") or "").strip()
    if command_kind not in {"python-unittest", "python-compile", "node-check"}:
        raise ValueError(f"Validation shard {shard_id} requests unsupported commandKind: {command_kind}")
    raw_targets = value.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError(f"Validation shard {shard_id} requires a non-empty targets list.")
    targets = tuple(str(item).strip() for item in raw_targets)
    if any(not item for item in targets) or len(targets) > 64:
        raise ValueError(f"Validation shard {shard_id} targets are invalid or exceed 64 entries.")
    return ShardSpec(
        shard_id=shard_id,
        command_kind=command_kind,
        targets=targets,
        repeat_count=require_integer(value.get("repeatCount"), f"Validation shard {shard_id} repeatCount", 1, 5),
        timeout_seconds=require_integer(value.get("timeoutSeconds"), f"Validation shard {shard_id} timeoutSeconds", 1, 300),
        memory_bytes=require_integer(
            value.get("memoryBytes"),
            f"Validation shard {shard_id} memoryBytes",
            64 * 1024 * 1024,
            2 * 1024 * 1024 * 1024,
        ),
    )


def confined_path(root: Path, relative: str, suffixes: frozenset[str]) -> Path:
    candidate = (root / relative).resolve()
    if Path(relative).is_absolute() or not candidate.is_relative_to(root.resolve()):
        raise ValueError(f"Validation target escapes the workspace: {relative}")
    if not candidate.is_file():
        raise ValueError(f"Validation target does not exist: {relative}")
    if candidate.suffix.lower() not in suffixes:
        raise ValueError(f"Validation target has an unsupported suffix: {relative}")
    return candidate


def shard_command(root: Path, spec: ShardSpec) -> tuple[str, ...]:
    if spec.command_kind == "python-unittest":
        if any(TARGET_PATTERN.fullmatch(target) is None for target in spec.targets):
            raise ValueError(f"Validation shard {spec.shard_id} has an invalid unittest selector.")
        return (sys.executable, "-m", "unittest", *spec.targets)
    if spec.command_kind == "python-compile":
        targets = tuple(confined_path(root, target, frozenset({".py"})) for target in spec.targets)
        return (sys.executable, "-m", "py_compile", *(str(path) for path in targets))
    executable = shutil.which("node")
    if executable is None:
        raise RuntimeError("Validation shard requested node-check but Node.js is unavailable.")
    if len(spec.targets) != 1:
        raise ValueError("A node-check shard requires exactly one target.")
    target = confined_path(root, spec.targets[0], frozenset({".js", ".mjs", ".cjs"}))
    return (executable, "--check", str(target))


def safe_environment() -> dict[str, str]:
    environment = {str(key): str(value) for key, value in os.environ.items() if key.upper() in ENVIRONMENT_ALLOWLIST}
    environment.update({"PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"})
    return environment


def redact_output(value: str) -> str:
    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def normalized_output(value: str, root: Path) -> str:
    normalized = value.replace(str(root.resolve()), "<WORKSPACE>").replace("\\", "/")
    normalized = DURATION_PATTERN.sub("elapsed <DURATION>", normalized)
    return "\n".join(line.rstrip() for line in normalized.splitlines()).strip()


def execute_validation_shard(root: Path, spec: ShardSpec, graph_fingerprint: str) -> dict[str, object]:
    resolved = root.resolve()
    if len(graph_fingerprint) != 64:
        raise ValueError("Validation shard requires the exact 64-character graph fingerprint.")
    command = shard_command(resolved, spec)
    input_payload = {"spec": asdict(spec), "graphFingerprint": graph_fingerprint, "command": list(command)}
    input_sha256 = canonical_sha256(input_payload)
    context = start_chronos_context(load_runtime_constitution().policy_epoch, 0)
    transcripts: list[dict[str, object]] = []
    for sequence in range(1, spec.repeat_count + 1):
        started = time.perf_counter_ns()
        timed_out = False
        try:
            result = brokered_run(command, resolved, safe_environment(), spec.timeout_seconds, spec.memory_bytes)
        except BrokerTimeoutExpired as error:
            timed_out = True
            result = {
                "returncode": 124,
                "output": str(error.output or ""),
                "isolation": error.isolation,
                "telemetry": error.telemetry,
            }
        elapsed_milliseconds = round((time.perf_counter_ns() - started) / 1_000_000, 3)
        raw_output = redact_output(result["output"])
        normalized = normalized_output(raw_output, resolved)
        isolation = result["isolation"]
        observed_at = datetime.now(timezone.utc)
        resource_charge = build_resource_charge(
            f"validation-shard:{spec.shard_id}:{sequence}",
            "validation-shard",
            context,
            result["telemetry"],
            {"providerCalls": 0, "totalTokens": 0, "costUsd": 0.0},
            "validation-authority",
            None,
            None,
            observed_at,
        )
        isolation_strength = (
            "WINDOWS_JOB_OBJECT"
            if isolation.get("kernelProcessLimit") is True
            else "INHERITED_PARENT_JOB"
            if isolation.get("broker") == "windows-parent-job-boundary"
            else "PROCESS_BOUNDARY_ONLY"
        )
        transcripts.append(
            {
                "sequence": sequence,
                "inputSha256": input_sha256,
                "returnCode": result["returncode"],
                "timedOut": timed_out,
                "elapsedMilliseconds": elapsed_milliseconds,
                "outputSha256": hashlib.sha256(raw_output.encode("utf-8")).hexdigest().upper(),
                "normalizedOutputSha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest().upper(),
                "outputTail": raw_output[-8_000:],
                "isolationStrength": isolation_strength,
                "isolation": isolation,
                "resourceCharge": resource_charge,
            }
        )
    passed = all(item["returnCode"] == 0 for item in transcripts)
    normalized_hashes = {str(item["normalizedOutputSha256"]) for item in transcripts}
    repeatable = len(normalized_hashes) == 1
    status = "PASS" if passed and repeatable else "NONDETERMINISTIC" if passed else "FAIL"
    report_without_digest = {
        "schema": SHARD_SCHEMA,
        "status": status,
        "spec": asdict(spec),
        "graphFingerprint": graph_fingerprint,
        "inputSha256": input_sha256,
        "repeatable": repeatable,
        "transcripts": transcripts,
        "summary": {
            "runs": len(transcripts),
            "passedRuns": sum(1 for item in transcripts if item["returnCode"] == 0),
            "meanElapsedMilliseconds": round(mean(float(item["elapsedMilliseconds"]) for item in transcripts), 3),
            "maximumElapsedMilliseconds": max(float(item["elapsedMilliseconds"]) for item in transcripts),
            "kernelLimitAppliedRuns": sum(
                1
                for item in transcripts
                if isinstance(item.get("isolation"), dict) and item["isolation"].get("kernelProcessLimit") is True
            ),
            "completeResourceCharges": sum(
                1
                for item in transcripts
                if isinstance(item.get("resourceCharge"), dict)
                and item["resourceCharge"].get("completeness") == "COMPLETE"
            ),
            "partialResourceCharges": sum(
                1
                for item in transcripts
                if isinstance(item.get("resourceCharge"), dict)
                and item["resourceCharge"].get("completeness") == "PARTIAL"
            ),
        },
        "authority": {
            "arbitraryCommands": False,
            "providerCalls": 0,
            "sourceMutation": False,
            "promotion": False,
            "kernelProcessLimitRequested": True,
            "kernelMemoryLimitGuaranteed": all(
                isinstance(item.get("isolation"), dict) and item["isolation"].get("kernelProcessLimit") is True
                for item in transcripts
            ),
        },
    }
    return report_without_digest | {"transcriptSha256": canonical_sha256(report_without_digest)}


def execute_shard_suite(
    root: Path,
    specs: Sequence[ShardSpec],
    graph_fingerprint: str,
    maximum_parallel: int,
) -> dict[str, object]:
    if not specs or len(specs) > 16:
        raise ValueError("A deterministic shard suite requires from one through sixteen shard specs.")
    if len({spec.shard_id for spec in specs}) != len(specs):
        raise ValueError("Deterministic shard ids must be unique within a suite.")
    if not isinstance(maximum_parallel, int) or isinstance(maximum_parallel, bool) or maximum_parallel < 1 or maximum_parallel > 4:
        raise ValueError("Deterministic shard maximumParallel must be an integer from one through four.")
    active_width = min(maximum_parallel, len(specs))
    with ThreadPoolExecutor(max_workers=active_width, thread_name_prefix="blunder-shard") as executor:
        reports = list(executor.map(lambda spec: execute_validation_shard(root, spec, graph_fingerprint), specs))
    status = "PASS" if all(report.get("status") == "PASS" for report in reports) else "FAIL"
    payload = {
        "schema": "obtuse.blunder.deterministic-shard-suite.v1",
        "status": status,
        "graphFingerprint": graph_fingerprint,
        "shardCount": len(reports),
        "reports": reports,
        "parallelism": {"requested": maximum_parallel, "activeWidth": active_width, "maximumAllowed": 4},
        "authority": {"parallelExecution": active_width > 1, "automaticPromotion": False, "sourceMutation": False},
    }
    return payload | {"suiteSha256": canonical_sha256(payload)}


def rank_shard_candidates(reports: Sequence[Mapping[str, object]]) -> dict[str, object]:
    candidates: list[dict[str, object]] = []
    for report in reports:
        spec = report.get("spec") if isinstance(report.get("spec"), dict) else {}
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        candidates.append(
            {
                "candidateId": str(spec.get("shard_id") or ""),
                "status": report.get("status"),
                "repeatable": report.get("repeatable") is True,
                "meanElapsedMilliseconds": float(summary.get("meanElapsedMilliseconds") or 0.0),
                "transcriptSha256": report.get("transcriptSha256"),
            }
        )
    ranked = sorted(
        candidates,
        key=lambda item: (
            item["status"] != "PASS",
            item["repeatable"] is not True,
            float(item["meanElapsedMilliseconds"]),
            str(item["candidateId"]),
        ),
    )
    eligible = [item for item in ranked if item["status"] == "PASS" and item["repeatable"] is True]
    return {
        "schema": "obtuse.blunder.deterministic-shard-tournament.v1",
        "status": "CHAMPION_AVAILABLE_FOR_QUORUM" if eligible else "NO_ELIGIBLE_CANDIDATE",
        "ranked": ranked,
        "champion": eligible[0] if eligible else None,
        "rankingPolicy": ["passing", "repeatable", "lowest measured mean elapsed time", "stable candidate id"],
        "automaticMerge": False,
        "sourceMutation": False,
    }
