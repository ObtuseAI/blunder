from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from blunder.capabilities import CapabilityRegistry, route_task_with_bias
from blunder.constitutional_adapters import (
    record_mission_cleanup_boundary,
    record_mission_integration_boundary,
    record_process_boundary,
    record_worktree_creation_boundary,
)
from blunder.missions.outcomes import outcome_bias, outcome_summary, record_outcome


MISSION_CONFIG_SCHEMA = "obtuse.blunder.mission-branch-config.v1"
MISSION_RECORD_SCHEMA = "obtuse.blunder.mission-branch.v1"
ACTIVE_STATUSES: frozenset[str] = frozenset({"preparing", "running", "cancelling"})
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled", "interrupted"})
MAX_MISSION_TASK_CHARS = 2_500
MAX_PATCH_PREVIEW_CHARS = 24_000


@dataclass(frozen=True)
class MissionBranchConfig:
    repository_root: Path
    workspace_root: Path
    max_parallel: int
    max_mission_seconds: int
    max_provider_calls: int
    max_tokens: int
    max_cost_usd: float
    retention_days: int


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_event_tail(path: Path, limit: int) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    events: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def run_git(
    root: Path,
    arguments: Sequence[str],
    input_bytes: bytes | None,
    check: bool,
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.run(
        ["git", "-C", str(root), *arguments],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    if check and process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Git {' '.join(arguments)} failed in {root}: {detail}")
    return process


def git_text(root: Path, arguments: Sequence[str]) -> str:
    return run_git(root, arguments, None, True).stdout.decode("utf-8", errors="replace").strip()


def require_git_repository(root: Path) -> Path:
    resolved = Path(git_text(root, ("rev-parse", "--show-toplevel"))).resolve()
    if resolved != root.resolve():
        raise ValueError(f"Mission repositoryRoot must be the Git top level: {root}")
    return resolved


def load_mission_config(root: Path) -> tuple[MissionBranchConfig | None, str | None]:
    path = root / "artifacts" / "obtuse" / "blunder-shell-v2" / "mission-branches.json"
    if not path.is_file():
        return None, f"Create {path} from the first-party example to enable isolated mission branches."
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read mission branch configuration {path}: {error}") from error
    if not isinstance(value, dict) or value.get("schema") != MISSION_CONFIG_SCHEMA:
        raise ValueError(f"Mission branch configuration has an unsupported schema: {path}")
    repository_root = Path(str(value.get("repositoryRoot") or "")).expanduser().resolve()
    workspace_root = Path(str(value.get("workspaceRoot") or "")).expanduser().resolve()
    if not repository_root.is_dir():
        raise ValueError(f"Mission repositoryRoot does not exist: {repository_root}")
    require_git_repository(repository_root)
    if workspace_root == repository_root or workspace_root.is_relative_to(repository_root):
        raise ValueError("Mission workspaceRoot must be outside the source repository.")
    maximum = value.get("maxParallel")
    if not isinstance(maximum, int) or maximum < 1 or maximum > 3:
        raise ValueError("Mission maxParallel must be an integer from one through three.")
    workspace_root.mkdir(parents=True, exist_ok=True)
    max_seconds = int(value.get("maxMissionSeconds") or 1_800)
    max_calls = int(value.get("maxProviderCalls") or 3)
    max_tokens = int(value.get("maxTokens") or 200_000)
    max_cost = float(value.get("maxCostUsd") or 5.0)
    retention_days = int(value.get("retentionDays") or 14)
    if max_seconds < 60 or max_seconds > 7_200:
        raise ValueError("Mission maxMissionSeconds must be from 60 through 7200.")
    if max_calls != 3:
        raise ValueError("Mission maxProviderCalls must be exactly three: strategy, implementation, and review.")
    if max_tokens < 1_000 or max_tokens > 1_000_000 or max_cost <= 0 or retention_days < 1:
        raise ValueError("Mission token, cost, and retention budgets are invalid.")
    return MissionBranchConfig(
        repository_root,
        workspace_root,
        maximum,
        max_seconds,
        max_calls,
        max_tokens,
        max_cost,
        retention_days,
    ), None


def worker_environment(
    config: MissionBranchConfig,
    event_log: Path,
    queue_depth: int,
    queue_capacity: int,
) -> dict[str, str]:
    allowed = {
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        "OPENROUTER_REVIEW_MODEL",
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
    environment = {str(key): str(value) for key, value in os.environ.items() if key.upper() in allowed}
    environment.update(
        {
            "BLUNDER_MISSION_EVENT_LOG": str(event_log),
            "BLUNDER_MISSION_MAX_SECONDS": str(config.max_mission_seconds),
            "BLUNDER_MISSION_MAX_PROVIDER_CALLS": str(config.max_provider_calls),
            "BLUNDER_MISSION_MAX_TOKENS": str(config.max_tokens),
            "BLUNDER_MISSION_MAX_COST_USD": str(config.max_cost_usd),
            "BLUNDER_MISSION_QUEUE_DEPTH": str(queue_depth),
            "BLUNDER_MISSION_QUEUE_CAPACITY": str(queue_capacity),
            "BLUNDER_CONTEXT_CACHE_ROOT": str(
                config.repository_root / "artifacts" / "obtuse" / "blunder-context-cache-v1"
            ),
            "BLUNDER_PROVIDER_CACHE_ROOT": str(
                config.repository_root / "artifacts" / "obtuse" / "blunder-provider-cache-v1"
            ),
        }
    )
    return environment


def copy_review_state(repository_root: Path, worktree: Path) -> dict[str, object]:
    tracked_patch = run_git(repository_root, ("diff", "--binary", "HEAD", "--"), None, True).stdout
    if tracked_patch:
        run_git(worktree, ("apply", "--binary", "--whitespace=nowarn", "-"), tracked_patch, True)
    untracked_bytes = run_git(
        repository_root,
        ("ls-files", "--others", "--exclude-standard", "-z"),
        None,
        True,
    ).stdout
    untracked = tuple(
        item.decode("utf-8", errors="strict") for item in untracked_bytes.split(b"\0") if item
    )
    resolved_repository_root = repository_root.resolve()
    resolved_worktree = worktree.resolve()
    for relative in untracked:
        source = repository_root / relative
        target = worktree / relative
        source_inside = source.resolve().is_relative_to(resolved_repository_root)
        target_inside = target.resolve().is_relative_to(resolved_worktree)
        if not source_inside or not target_inside:
            raise ValueError(f"Review-state path escapes the mission boundary: {relative}")
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=False)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    run_git(worktree, ("add", "-A", "--"), None, True)
    seed_patch = run_git(worktree, ("diff", "--cached", "--binary", "--"), None, True).stdout
    return {
        "trackedPatchBytes": len(tracked_patch),
        "untrackedPaths": list(untracked),
        "seedPatch": seed_patch,
        "seedPatchSha256": hashlib.sha256(seed_patch).hexdigest().upper(),
    }


def capability_contract(route: Mapping[str, object]) -> dict[str, object]:
    raw_skills = route.get("skills")
    raw_tools = route.get("tools")
    skills = raw_skills if isinstance(raw_skills, list) else []
    tools = raw_tools if isinstance(raw_tools, list) else []
    return {
        "skills": [
            {
                "id": str(item.get("skill_id") or ""),
                "name": str(item.get("name") or ""),
                "workflow": [str(step) for step in item.get("workflow", [])][:5],
            }
            for item in skills
            if isinstance(item, dict)
        ],
        "tools": [str(item.get("tool_id") or "") for item in tools if isinstance(item, dict)],
        "permissions": [str(item) for item in route.get("permissions", [])],
        "providerRequired": route.get("providerRequired") is True,
        "sourceMutationPossible": route.get("sourceMutationPossible") is True,
    }


def worker_task(task: str, contract: Mapping[str, object]) -> str:
    compact = json.dumps(contract, separators=(",", ":"), ensure_ascii=False)
    instruction = (
        "Operate as one isolated Blunder mission branch. Stay inside this worktree and the routed first-party "
        "capability contract. Make one cohesive bounded change, run proof, and leave the primary review tree untouched."
    )
    combined = f"{task}\n\nMISSION BRANCH CONTRACT\n{instruction}\n{compact}"
    if len(combined) > 4_000:
        raise ValueError("The task and routed capability contract exceed the native agent context ceiling.")
    return combined


def process_is_running(pid: int) -> bool:
    if pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def process_identity_matches(pid: int, identity_token: str) -> bool:
    if not process_is_running(pid) or not identity_token:
        return False
    if os.name == "nt":
        command = (
            f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" -ErrorAction SilentlyContinue;"
            "if($p){[Console]::Out.Write($p.CommandLine)}"
        )
        process = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        return process.returncode == 0 and identity_token in process.stdout and "blunder_mission_worker.py" in process.stdout
    commandline = Path(f"/proc/{pid}/cmdline")
    return commandline.is_file() and identity_token in commandline.read_text(encoding="utf-8", errors="replace")


def seeded_index_object_id(worktree: Path, relative: str) -> str | None:
    process = run_git(worktree, ("rev-parse", f":{relative}"), None, False)
    return process.stdout.decode("ascii", errors="replace").strip() if process.returncode == 0 else None


def primary_worktree_object_id(repository_root: Path, relative: str) -> str | None:
    path = repository_root / relative
    if not path.is_file():
        return None
    return git_text(repository_root, ("hash-object", "--path", relative, "--", relative))


def finalize_mission_patch(config: MissionBranchConfig, mission_path: Path) -> dict[str, object]:
    record_path = mission_path / "record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError(f"Mission record is invalid: {record_path}")
    worktree = Path(str(record["worktree"])).resolve()
    new_files_bytes = run_git(worktree, ("ls-files", "--others", "--exclude-standard", "-z"), None, True).stdout
    new_files = tuple(
        item.decode("utf-8", errors="strict")
        for item in new_files_bytes.split(b"\0")
        if item and not set(Path(item.decode("utf-8", errors="strict")).parts).intersection({"artifacts", "__pycache__"})
    )
    if new_files:
        run_git(worktree, ("add", "-N", "--", *new_files), None, True)
    patchspec = ("--", ".", ":(exclude)artifacts/**", ":(exclude)**/__pycache__/**")
    patch = run_git(worktree, ("diff", "--binary", *patchspec), None, True).stdout
    changed_bytes = run_git(worktree, ("diff", "--name-only", "-z", *patchspec), None, True).stdout
    changed = tuple(item.decode("utf-8", errors="strict") for item in changed_bytes.split(b"\0") if item)
    patch_path = mission_path / "mission-only.patch"
    patch_path.write_bytes(patch)
    compatibility: list[dict[str, object]] = []
    for relative in changed:
        seeded_hash = None if relative in new_files else seeded_index_object_id(worktree, relative)
        primary_hash = primary_worktree_object_id(config.repository_root, relative)
        compatible = primary_hash is None if seeded_hash is None else primary_hash == seeded_hash
        compatibility.append(
            {
                "path": relative,
                "seededObjectId": seeded_hash,
                "primaryObjectId": primary_hash,
                "compatible": compatible,
            }
        )
    raw_claims = record.get("claimedPaths")
    claims = tuple(str(item).strip("/") for item in raw_claims) if isinstance(raw_claims, list) else ()
    claim_violations = [
        relative
        for relative in changed
        if claims and not any(relative == claim or relative.startswith(claim + "/") for claim in claims)
    ]
    ready = bool(changed) and not claim_violations and all(bool(item["compatible"]) for item in compatibility)
    return {
        "changedFiles": list(changed),
        "patch": str(patch_path),
        "patchBytes": len(patch),
        "patchSha256": hashlib.sha256(patch).hexdigest().upper(),
        "patchPreview": patch.decode("utf-8", errors="replace")[:MAX_PATCH_PREVIEW_CHARS],
        "primaryCompatibility": compatibility,
        "claimViolations": claim_violations,
        "integrationState": (
            "READY_TO_APPLY"
            if ready
            else ("NO_CHANGES" if not changed else ("CLAIM_VIOLATION" if claim_violations else "PRIMARY_DRIFTED"))
        ),
    }


class MissionBranchManager:
    def __init__(self, root: Path, registry: CapabilityRegistry, python_executable: str) -> None:
        self._root = root.resolve()
        self._registry = registry
        self._python = python_executable
        self._config, self._unavailable_reason = load_mission_config(self._root)
        self._state_root = self._root / "artifacts" / "obtuse" / "blunder-mission-branches-v1"
        self._state_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}

    def _record_path(self, mission_id: str) -> Path:
        if not mission_id or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in mission_id):
            raise ValueError("Mission id is invalid.")
        return self._state_root / mission_id / "record.json"

    def _read(self, mission_id: str) -> dict[str, object]:
        path = self._record_path(mission_id)
        if not path.is_file():
            raise ValueError("Mission branch does not exist.")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema") != MISSION_RECORD_SCHEMA:
            raise ValueError(f"Mission branch record is invalid: {path}")
        return value

    def _write(self, record: Mapping[str, object]) -> None:
        atomic_write_json(self._record_path(str(record["id"])), record)

    def _records(self) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for path in sorted(self._state_root.glob("*/record.json"), reverse=True):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and value.get("schema") == MISSION_RECORD_SCHEMA:
                records.append(value)
        return records

    def _refresh_record(self, record: dict[str, object]) -> dict[str, object]:
        if str(record.get("status")) not in ACTIVE_STATUSES:
            return record
        mission_id = str(record["id"])
        mission_path = self._state_root / mission_id
        result_path = mission_path / "worker-result.json"
        process = self._processes.get(mission_id)
        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if process is not None:
                process.wait(timeout=5)
            patch = finalize_mission_patch(self._config_required(), mission_path)
            record.update(patch)
            record["worker"] = result
            record["status"] = "completed" if result.get("ok") is True else "failed"
            record["error"] = None if result.get("ok") is True else str(result.get("error") or "Worker failed.")
            record["finishedAt"] = utc_now()
            self._processes.pop(mission_id, None)
            self._write(record)
            return record
        if process is not None and process.poll() is not None:
            record["status"] = "interrupted"
            record["error"] = f"Mission worker exited with code {process.returncode} without a result record."
            record["finishedAt"] = utc_now()
            self._processes.pop(mission_id, None)
            self._write(record)
            return record
        pid = int(record.get("pid") or 0)
        identity_token = str(record.get("identityToken") or "")
        if process is None and not process_identity_matches(pid, identity_token):
            record["status"] = "interrupted"
            record["error"] = "Mission worker is no longer running and emitted no result record."
            record["finishedAt"] = utc_now()
            self._write(record)
        return record

    def _config_required(self) -> MissionBranchConfig:
        if self._config is None:
            raise RuntimeError(self._unavailable_reason or "Mission branch execution is unavailable.")
        return self._config

    def source_root(self) -> Path:
        return self._config_required().repository_root

    def execution_budget(self) -> dict[str, int | float]:
        config = self._config_required()
        return {
            "seconds": config.max_mission_seconds,
            "providerCalls": config.max_provider_calls,
            "tokens": config.max_tokens,
            "costUsd": config.max_cost_usd,
        }

    def state(self) -> dict[str, object]:
        with self._lock:
            records: list[dict[str, object]] = []
            for record in self._records():
                if str(record.get("status")) in ACTIVE_STATUSES and float(record.get("deadlineEpoch") or 0) < time.time():
                    record = self.cancel(str(record["id"]))
                    record["error"] = "Mission exceeded its hard elapsed-time budget."
                    self._write(record)
                refreshed = self._refresh_record(record)
                finished = str(refreshed.get("finishedAt") or "")
                age_days = 0.0
                if finished:
                    age_days = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(finished)).total_seconds() / 86_400)
                refreshed["retention"] = {
                    "days": self._config.retention_days if self._config is not None else None,
                    "ageDays": round(age_days, 3),
                    "prunable": bool(
                        self._config is not None
                        and str(refreshed.get("status")) in TERMINAL_STATUSES
                        and not refreshed.get("worktreeRemoved")
                        and age_days >= self._config.retention_days
                    ),
                }
                event_path = Path(str(refreshed.get("events") or ""))
                refreshed["eventTail"] = read_event_tail(event_path, 12)
                worker = refreshed.get("worker") if isinstance(refreshed.get("worker"), dict) else {}
                report = worker.get("report") if isinstance(worker.get("report"), dict) else {}
                refreshed["budgetUsage"] = report.get("budget")
                refreshed["resourceCharge"] = report.get("resourceCharge") or worker.get("resourceCharge")
                records.append(refreshed)
            active = sum(1 for record in records if str(record.get("status")) in ACTIVE_STATUSES)
            maximum = self._config.max_parallel if self._config is not None else 0
            return {
                "schema": "obtuse.blunder.mission-control-branches.v1",
                "available": self._config is not None,
                "unavailableReason": self._unavailable_reason,
                "capacity": {"active": active, "maximum": maximum, "available": max(0, maximum - active)},
                "repositoryRoot": str(self._config.repository_root) if self._config is not None else None,
                "workspaceRoot": str(self._config.workspace_root) if self._config is not None else None,
                "missions": records[:30],
                "isolation": {
                    "separateProcesses": True,
                    "separateContexts": True,
                    "gitWorktrees": True,
                    "primaryTreeMutationDuringMission": False,
                    "integrationRequiresExactConfirmation": True,
                    "firstPartyCapabilitiesOnly": True,
                },
            }

    def get(self, mission_id: str) -> dict[str, object]:
        with self._lock:
            return self._refresh_record(self._read(mission_id))

    def start(self, task: str, provider_approved: bool) -> dict[str, object]:
        return self._start(task, provider_approved, (), None, "specialist")

    def start_with_standing_policy(self, task: str) -> dict[str, object]:
        return self._start(task, True, (), None, "specialist")

    def start_claimed(
        self,
        task: str,
        provider_approved: bool,
        claimed_paths: Sequence[str],
        program_id: str,
        role: str,
    ) -> dict[str, object]:
        claims = tuple(dict.fromkeys(path.replace("\\", "/").strip("/") for path in claimed_paths))
        if not claims or any(not path or path.startswith(".") or ".." in Path(path).parts for path in claims):
            raise ValueError("A mission program requires safe non-empty file claims.")
        return self._start(task, provider_approved, claims, program_id, role)

    def _start(
        self,
        task: str,
        provider_approved: bool,
        claimed_paths: Sequence[str],
        program_id: str | None,
        role: str,
    ) -> dict[str, object]:
        normalized = task.strip()
        if len(normalized) < 3 or len(normalized) > MAX_MISSION_TASK_CHARS:
            raise ValueError(f"Mission task must contain 3 through {MAX_MISSION_TASK_CHARS} characters.")
        if not provider_approved:
            raise ValueError("An isolated mission branch requires explicit native model approval.")
        config = self._config_required()
        learning = outcome_summary(self._root, [skill.skill_id for skill in self._registry.skills])
        route = route_task_with_bias(self._registry, normalized, 5, outcome_bias(learning))
        if route.get("sourceMutationPossible") is not True or route.get("providerRequired") is not True:
            raise ValueError("This task routed to advisory-only capabilities and does not need a coding worktree.")
        contract = capability_contract(route)
        selected_skill_ids = [
            str(item.get("id") or "")
            for item in contract.get("skills", [])
            if isinstance(item, dict)
        ]
        contract["outcomeLearning"] = outcome_summary(self._root, selected_skill_ids)
        expanded_task = worker_task(normalized, contract)
        with self._lock:
            state = self.state()
            capacity = state["capacity"]
            if not isinstance(capacity, dict) or int(capacity["available"]) < 1:
                raise ValueError(f"Mission capacity is full at {config.max_parallel} parallel workers.")
            mission_id = f"m-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            branch = f"codex/mission-{mission_id[2:]}"
            worktree = (config.workspace_root / mission_id).resolve()
            if not worktree.is_relative_to(config.workspace_root) or worktree.exists():
                raise ValueError(f"Mission worktree path is unsafe or already exists: {worktree}")
            mission_path = self._state_root / mission_id
            mission_path.mkdir(parents=True, exist_ok=False)
            record: dict[str, object] = {
                "schema": MISSION_RECORD_SCHEMA,
                "id": mission_id,
                "task": normalized,
                "status": "preparing",
                "createdAt": utc_now(),
                "startedAt": None,
                "finishedAt": None,
                "branch": branch,
                "baseHead": git_text(config.repository_root, ("rev-parse", "HEAD")),
                "worktree": str(worktree),
                "capabilities": contract,
                "claimedPaths": list(claimed_paths),
                "programId": program_id,
                "role": role,
                "pid": None,
                "error": None,
                "integrationState": "PENDING",
                "budget": {
                    "seconds": config.max_mission_seconds,
                    "providerCalls": config.max_provider_calls,
                    "tokens": config.max_tokens,
                    "costUsd": config.max_cost_usd,
                },
                "deadlineEpoch": time.time() + config.max_mission_seconds,
            }
            self._write(record)
            try:
                worktree_decision = record_worktree_creation_boundary(
                    self._root,
                    mission_id,
                    normalized,
                    datetime.now(timezone.utc),
                )
                record["constitutionalDecision"] = {"worktree": worktree_decision}
                run_git(
                    config.repository_root,
                    ("worktree", "add", "-b", branch, str(worktree), "HEAD"),
                    None,
                    True,
                )
                seed = copy_review_state(config.repository_root, worktree)
                seed_patch = seed.pop("seedPatch")
                assert isinstance(seed_patch, bytes)
                (mission_path / "seed-review-state.patch").write_bytes(seed_patch)
                record["seed"] = seed
                result_path = mission_path / "worker-result.json"
                log_path = mission_path / "worker.log"
                event_log = mission_path / "events.jsonl"
                identity_token = f"worker-{uuid.uuid4().hex}"
                worker_script = self._root / "scripts" / "obtuse" / "blunder_mission_worker.py"
                if not worker_script.is_file():
                    raise RuntimeError(f"First-party mission worker is missing: {worker_script}")
                command = [
                    self._python,
                    str(worker_script),
                    "--root",
                    str(worktree),
                    "--task",
                    expanded_task,
                    "--result",
                    str(result_path),
                    "--provider-approved",
                    "--identity-token",
                    identity_token,
                ]
                flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | int(
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
                process_decision = record_process_boundary(
                    self._root,
                    "mission-branch-manager",
                    "mission-worker",
                    command,
                    mission_id,
                    datetime.now(timezone.utc),
                )
                constitutional = record.get("constitutionalDecision")
                if not isinstance(constitutional, dict):
                    raise TypeError("mission constitutional decision record must be an object")
                constitutional["workerProcess"] = process_decision
                with log_path.open("wb") as log_handle:
                    process = subprocess.Popen(
                        command,
                        cwd=worktree,
                        stdin=subprocess.DEVNULL,
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                        env=worker_environment(
                            config,
                            event_log,
                            int(capacity["active"]) + 1,
                            config.max_parallel,
                        ),
                        creationflags=flags,
                    )
                self._processes[mission_id] = process
                record["status"] = "running"
                record["startedAt"] = utc_now()
                record["pid"] = process.pid
                record["log"] = str(log_path)
                record["events"] = str(event_log)
                record["identityToken"] = identity_token
                self._write(record)
                return record
            except Exception:
                if worktree.exists():
                    run_git(config.repository_root, ("worktree", "remove", "--force", str(worktree)), None, False)
                run_git(config.repository_root, ("branch", "-D", branch), None, False)
                record["status"] = "failed"
                record["finishedAt"] = utc_now()
                self._write(record)
                raise

    def cancel(self, mission_id: str) -> dict[str, object]:
        with self._lock:
            record = self._refresh_record(self._read(mission_id))
            if str(record.get("status")) not in ACTIVE_STATUSES:
                return record
            record["status"] = "cancelling"
            self._write(record)
            process = self._processes.get(mission_id)
            pid = int(record.get("pid") or 0)
            if process is not None and process.poll() is None:
                pid = process.pid
            if pid > 0 and process_is_running(pid):
                if os.name == "nt":
                    command = ["taskkill", "/PID", str(pid), "/T", "/F"]
                    record_process_boundary(
                        self._root,
                        "mission-branch-manager",
                        "mission-worker-cancellation",
                        command,
                        mission_id,
                        datetime.now(timezone.utc),
                    )
                    subprocess.run(
                        command,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
                    )
                elif process is not None:
                    process.terminate()
            if process is not None:
                process.wait(timeout=5)
            record["status"] = "cancelled"
            record["finishedAt"] = utc_now()
            record["error"] = "Stopped by the Mission Control operator."
            self._processes.pop(mission_id, None)
            self._write(record)
            return record

    def integrate(self, mission_id: str, confirmation: str) -> dict[str, object]:
        with self._lock:
            record = self._refresh_record(self._read(mission_id))
            completed = str(record.get("status")) == "completed"
            expected = f"INTEGRATE {mission_id}"
            exact_confirmation = confirmation == expected
            if not completed:
                record_mission_integration_boundary(
                    self._root,
                    mission_id,
                    False,
                    exact_confirmation,
                    False,
                    datetime.now(timezone.utc),
                )
                raise ValueError("Only a successfully completed mission branch can be integrated.")
            if not exact_confirmation:
                record_mission_integration_boundary(
                    self._root,
                    mission_id,
                    True,
                    False,
                    record.get("integrationState") == "READY_TO_APPLY",
                    datetime.now(timezone.utc),
                )
                raise ValueError(f"Mission integration requires exact confirmation: {expected}")
            config = self._config_required()
            refreshed = finalize_mission_patch(config, self._state_root / mission_id)
            record.update(refreshed)
            integration_shadow = record_mission_integration_boundary(
                self._root,
                mission_id,
                True,
                True,
                record.get("integrationState") == "READY_TO_APPLY",
                datetime.now(timezone.utc),
            )
            record["constitutionalShadow"] = {"mode": "fail_closed", "integration": integration_shadow}
            if record.get("integrationState") != "READY_TO_APPLY":
                self._write(record)
                raise ValueError(f"Mission patch is not safe to integrate: {record.get('integrationState')}")
            patch_path = Path(str(record["patch"])).resolve()
            patch_bytes = patch_path.read_bytes()
            run_git(config.repository_root, ("apply", "--check", "--binary", "-"), patch_bytes, True)
            run_git(config.repository_root, ("apply", "--binary", "-"), patch_bytes, True)
            record["integrationState"] = "APPLIED_TO_REVIEW_TREE"
            record["integratedAt"] = utc_now()
            record["integrationConfirmation"] = expected
            self._write(record)
            record_outcome(self._root, record, "integrated", None, {}, "Guarded mission patch integrated.")
            return record

    def outcome(
        self,
        mission_id: str,
        status: str,
        score: int | None,
        metrics: Mapping[str, object],
        note: str,
    ) -> dict[str, object]:
        with self._lock:
            record = self._refresh_record(self._read(mission_id))
            if str(record.get("status")) not in TERMINAL_STATUSES:
                raise ValueError("Mission outcomes can be recorded only after execution is terminal.")
            outcome = record_outcome(self._root, record, status, score, metrics, note)
            record["latestOutcome"] = outcome
            self._write(record)
            return outcome

    def reconcile(self, mission_id: str, confirmation: str) -> dict[str, object]:
        with self._lock:
            record = self._refresh_record(self._read(mission_id))
            expected = f"RECONCILE {mission_id}"
            if confirmation != expected:
                raise ValueError(f"Mission reconciliation requires exact confirmation: {expected}")
            refreshed = finalize_mission_patch(self._config_required(), self._state_root / mission_id)
            record.update(refreshed)
            if record.get("integrationState") != "PRIMARY_DRIFTED":
                raise ValueError("Mission reconciliation is available only for a primary-drifted patch.")
            config = self._config_required()
            old_worktree = Path(str(record["worktree"])).resolve()
            old_branch = str(record["branch"])
            reconcile_branch = f"codex/reconcile-{mission_id}-{uuid.uuid4().hex[:5]}"
            reconcile_worktree = (config.workspace_root / f"{mission_id}-reconcile-{uuid.uuid4().hex[:5]}").resolve()
            reconciliation_decision = record_worktree_creation_boundary(
                self._root,
                mission_id,
                str(record.get("task") or "reconcile mission patch"),
                datetime.now(timezone.utc),
            )
            record["constitutionalDecision"] = {"reconciliationWorktree": reconciliation_decision}
            run_git(config.repository_root, ("worktree", "add", "-b", reconcile_branch, str(reconcile_worktree), "HEAD"), None, True)
            seed = copy_review_state(config.repository_root, reconcile_worktree)
            seed_patch = seed.pop("seedPatch")
            assert isinstance(seed_patch, bytes)
            mission_path = self._state_root / mission_id
            (mission_path / "reconcile-seed-review-state.patch").write_bytes(seed_patch)
            merge_root = mission_path / "reconcile-inputs"
            merge_root.mkdir(parents=True, exist_ok=True)
            conflicts: list[str] = []
            for index, relative in enumerate(str(item) for item in record.get("changedFiles", [])):
                base_process = run_git(old_worktree, ("show", f":{relative}"), None, False)
                base = base_process.stdout if base_process.returncode == 0 else None
                current_path = reconcile_worktree / relative
                theirs_path = old_worktree / relative
                current = current_path.read_bytes() if current_path.is_file() else None
                theirs = theirs_path.read_bytes() if theirs_path.is_file() else None
                if base is None and theirs is not None:
                    if current is None:
                        current_path.parent.mkdir(parents=True, exist_ok=True)
                        current_path.write_bytes(theirs)
                    elif current != theirs:
                        conflicts.append(relative)
                    continue
                if base is not None and theirs is None:
                    if seeded_index_object_id(reconcile_worktree, relative) == seeded_index_object_id(old_worktree, relative):
                        if current_path.exists():
                            current_path.unlink()
                    else:
                        conflicts.append(relative)
                    continue
                if base is None or current is None or theirs is None:
                    conflicts.append(relative)
                    continue
                if b"\0" in base + current + theirs:
                    if current == base:
                        current_path.write_bytes(theirs)
                    elif current != theirs:
                        conflicts.append(relative)
                    continue
                base_path = merge_root / f"{index}-base"
                current_input = merge_root / f"{index}-current"
                theirs_input = merge_root / f"{index}-theirs"
                base_path.write_bytes(base)
                current_input.write_bytes(current)
                theirs_input.write_bytes(theirs)
                merge_command = ["git", "merge-file", "-p", str(current_input), str(base_path), str(theirs_input)]
                record_process_boundary(
                    self._root,
                    "mission-branch-manager",
                    "mission-reconciliation",
                    merge_command,
                    mission_id,
                    datetime.now(timezone.utc),
                )
                merge = subprocess.run(
                    merge_command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
                )
                if merge.returncode > 1:
                    raise RuntimeError(f"Git merge-file failed for {relative}: {merge.stderr.decode('utf-8', errors='replace')}")
                current_path.write_bytes(merge.stdout)
                if merge.returncode == 1:
                    conflicts.append(relative)
            record["supersededWorktrees"] = [
                *record.get("supersededWorktrees", []),
                {"worktree": str(old_worktree), "branch": old_branch},
            ]
            record["worktree"] = str(reconcile_worktree)
            record["branch"] = reconcile_branch
            record["seed"] = seed
            self._write(record)
            record.update(finalize_mission_patch(config, mission_path))
            record["reconciledAt"] = utc_now()
            if conflicts:
                record["integrationState"] = "CONFLICT_REVIEW_REQUIRED"
                record["conflictFiles"] = conflicts
            self._write(record)
            return record

    def cleanup(self, mission_id: str, confirmation: str) -> dict[str, object]:
        with self._lock:
            record = self._refresh_record(self._read(mission_id))
            terminal = str(record.get("status")) in TERMINAL_STATUSES
            exact_confirmation = confirmation == f"CLEAN {mission_id}"
            if not terminal:
                record_mission_cleanup_boundary(
                    self._root,
                    mission_id,
                    False,
                    exact_confirmation,
                    False,
                    datetime.now(timezone.utc),
                )
                raise ValueError("An active mission cannot be cleaned up.")
            expected = f"CLEAN {mission_id}"
            if not exact_confirmation:
                record_mission_cleanup_boundary(
                    self._root,
                    mission_id,
                    True,
                    False,
                    False,
                    datetime.now(timezone.utc),
                )
                raise ValueError(f"Mission cleanup requires exact confirmation: {expected}")
            config = self._config_required()
            targets = [
                {"worktree": record.get("worktree"), "branch": record.get("branch")},
                *[item for item in record.get("supersededWorktrees", []) if isinstance(item, dict)],
            ]
            paths_contained = all(
                Path(str(target.get("worktree") or "")).resolve().is_relative_to(config.workspace_root)
                for target in targets
            )
            cleanup_decision = record_mission_cleanup_boundary(
                self._root,
                mission_id,
                True,
                True,
                paths_contained,
                datetime.now(timezone.utc),
            )
            record["constitutionalDecision"] = {"cleanup": cleanup_decision}
            removed: list[str] = []
            for target in targets:
                worktree = Path(str(target.get("worktree") or "")).resolve()
                branch = str(target.get("branch") or "")
                if not worktree.is_relative_to(config.workspace_root):
                    raise ValueError(f"Refusing to clean a worktree outside the mission root: {worktree}")
                if worktree.exists():
                    run_git(config.repository_root, ("worktree", "remove", "--force", str(worktree)), None, True)
                if branch.startswith("codex/"):
                    run_git(config.repository_root, ("branch", "-D", branch), None, False)
                removed.append(str(worktree))
            record["worktreeRemoved"] = True
            record["removedWorktrees"] = removed
            record["archivedAt"] = utc_now()
            self._write(record)
            return record

    def synthesize(self, program_id: str, task: str, mission_ids: Sequence[str]) -> dict[str, object]:
        from blunder.native_agent import ProposedChange, run_validation, trusted_python_callers

        with self._lock:
            config = self._config_required()
            specialists = [self._refresh_record(self._read(mission_id)) for mission_id in mission_ids]
            if not specialists or any(record.get("integrationState") != "READY_TO_APPLY" for record in specialists):
                raise ValueError("Every specialist must have a drift-clean reviewed patch before synthesis.")
            claims = tuple(
                dict.fromkeys(
                    str(path)
                    for record in specialists
                    for path in record.get("claimedPaths", [])
                )
            )
            mission_id = f"s-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            branch = f"codex/synthesis-{mission_id[2:]}"
            worktree = (config.workspace_root / mission_id).resolve()
            mission_path = self._state_root / mission_id
            mission_path.mkdir(parents=True, exist_ok=False)
            record: dict[str, object] = {
                "schema": MISSION_RECORD_SCHEMA,
                "id": mission_id,
                "task": task,
                "status": "preparing",
                "createdAt": utc_now(),
                "startedAt": utc_now(),
                "finishedAt": None,
                "branch": branch,
                "baseHead": git_text(config.repository_root, ("rev-parse", "HEAD")),
                "worktree": str(worktree),
                "capabilities": {"skills": [{"name": "Mission Synthesis"}], "tools": [], "permissions": ["source:write-bounded", "tests:run"]},
                "claimedPaths": list(claims),
                "programId": program_id,
                "role": "synthesis",
                "pid": os.getpid(),
                "error": None,
                "integrationState": "PENDING",
                "sourceMissions": list(mission_ids),
            }
            self._write(record)
            try:
                worktree_decision = record_worktree_creation_boundary(
                    self._root,
                    mission_id,
                    task,
                    datetime.now(timezone.utc),
                )
                record["constitutionalDecision"] = {"synthesisWorktree": worktree_decision}
                run_git(config.repository_root, ("worktree", "add", "-b", branch, str(worktree), "HEAD"), None, True)
                seed = copy_review_state(config.repository_root, worktree)
                seed_patch = seed.pop("seedPatch")
                assert isinstance(seed_patch, bytes)
                (mission_path / "seed-review-state.patch").write_bytes(seed_patch)
                record["seed"] = seed
                for specialist in specialists:
                    patch = Path(str(specialist["patch"])).read_bytes()
                    run_git(worktree, ("apply", "--binary", "-"), patch, True)
                changed_bytes = run_git(worktree, ("diff", "--name-only", "-z", "--"), None, True).stdout
                changed = tuple(item.decode("utf-8") for item in changed_bytes.split(b"\0") if item)
                changes = tuple(
                    ProposedChange(path, (worktree / path).read_text(encoding="utf-8", errors="replace"), "synthesis")
                    for path in changed
                    if (worktree / path).is_file()
                )
                validation_artifact = mission_path / "validation"
                validation_artifact.mkdir(parents=True)
                validation = run_validation(worktree, changes, validation_artifact, trusted_python_callers(worktree, changes))
                if validation.get("testsPassed") is not True:
                    raise RuntimeError(f"Synthesis integration validation failed: {validation.get('testOutput')}")
                record.update(finalize_mission_patch(config, mission_path))
                record["worker"] = {
                    "ok": True,
                    "report": {
                        "status": "SYNTHESIZED_AND_VALIDATED",
                        "validation": validation,
                        "independentReviews": [record.get("worker", {}).get("report", {}).get("independentReview") for record in specialists],
                    },
                }
                record["status"] = "completed"
                record["finishedAt"] = utc_now()
                self._write(record)
                return record
            except Exception as error:
                record["status"] = "failed"
                record["finishedAt"] = utc_now()
                record["error"] = str(error)
                self._write(record)
                raise
