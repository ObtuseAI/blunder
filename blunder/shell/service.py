from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from blunder.constitutional_adapters import record_process_boundary
from blunder.shell.intelligence import task_briefing, workspace_search
from blunder.semantic_editor import document_analysis, save_document, symbol_definitions


TEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        ".css",
        ".cs",
        ".cfg",
        ".dart",
        ".gd",
        ".glsl",
        ".godot",
        ".gradle",
        ".hlsl",
        ".html",
        ".ini",
        ".js",
        ".jsx",
        ".json",
        ".jsonl",
        ".md",
        ".mjs",
        ".cjs",
        ".kt",
        ".kts",
        ".ps1",
        ".psm1",
        ".py",
        ".sh",
        ".scss",
        ".shader",
        ".svelte",
        ".svg",
        ".swift",
        ".toml",
        ".ts",
        ".tsx",
        ".vue",
        ".wgsl",
        ".xml",
        ".txt",
        ".yaml",
        ".yml",
    }
)

BLOCKED_FILE_PARTS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        ".venv-blunder-shell",
        "__pycache__",
        "node_modules",
        "artifacts",
    }
)

BLOCKED_EXACT_FILES: frozenset[str] = frozenset(
    {
        ".env",
        "configs/obtuse/blunder-operator-autonomy-authorization.json",
    }
)

TASK_BLOCK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("live benchmark control", re.compile(r"\b(?:launch|start|resume|upload|publish)\b.{0,40}\bbenchmark\b", re.I)),
    ("public publication", re.compile(r"\b(?:public submission|publish publicly|make public)\b", re.I)),
    ("Git history mutation", re.compile(r"\bgit\s+(?:push|commit|reset|rebase|clean)\b", re.I)),
    ("credential handling", re.compile(r"\b(?:print|show|rotate|change|export)\b.{0,32}\b(?:secret|credential|api key|token)\b", re.I)),
    ("destructive filesystem action", re.compile(r"\b(?:delete recursively|remove recursively|format drive|wipe)\b", re.I)),
)

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-or-v1-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(
        r"(?i)\b(?:OPENAI_API_KEY|OPENROUTER_API_KEY|GITHUB_TOKEN|GH_TOKEN|AWS_SECRET_ACCESS_KEY)\s*=\s*[^\s]+"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{20,}"),
)


@dataclass(frozen=True)
class ActionSpec:
    action_id: str
    label: str
    description: str
    provider_required: bool
    timeout_seconds: int


ACTION_SPECS: tuple[ActionSpec, ...] = (
    ActionSpec("status", "Refresh workspace", "Read live status and evolution state.", False, 45),
    ActionSpec("evolve", "Refresh evolution", "Rebuild the advisory compound-evolution plan.", False, 90),
    ActionSpec("planning_gate", "Planning gate", "Run the no-provider planning and coding quality gate.", False, 90),
    ActionSpec("tests", "Safety tests", "Run Blunder integration and safety tests.", False, 180),
    ActionSpec("readiness", "Capability readiness", "Run the canonical S-tier capability and safety audit.", False, 300),
    ActionSpec("run_task", "Run task", "Send one bounded coding task to Blunder Code.", True, 3600),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def parse_json_output(output: str) -> dict[str, object]:
    stripped = output.strip()
    if not stripped:
        raise RuntimeError("command returned no JSON")
    start = stripped.find("{")
    if start < 0:
        raise RuntimeError("command output did not contain a JSON object")
    value = json.loads(stripped[start:])
    if not isinstance(value, dict):
        raise RuntimeError("command JSON must be an object")
    return value


def process_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def run_json_command(root: Path, command: Sequence[str], timeout_seconds: int) -> dict[str, object]:
    record_process_boundary(
        root,
        "authenticated-shell",
        "shell-json-command",
        command,
        None,
        datetime.now(timezone.utc),
    )
    completed = subprocess.run(
        list(command),
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
        check=False,
        creationflags=process_flags(),
    )
    if completed.returncode != 0:
        error = redact_text(completed.stderr or completed.stdout)
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {error[-1000:]}")
    return parse_json_output(completed.stdout)


def latest_json(directory: Path, pattern: str) -> dict[str, object] | None:
    if not directory.is_dir():
        return None
    matches = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return read_json(matches[0]) if matches else None


def queue_summary(root: Path) -> dict[str, object]:
    path = root / "artifacts" / "obtuse" / "autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle" / "idle-task-queue.json"
    payload = read_json(path) or {}
    tasks = payload.get("tasks") if isinstance(payload.get("tasks"), list) else []
    counts: dict[str, int] = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        status = str(task.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {"total": len(tasks), "counts": counts, "path": str(path), "available": path.is_file()}


def shell_artifact_root(root: Path) -> Path:
    return root / "artifacts" / "obtuse" / "blunder-shell-v2"


def workspace_state(root: Path) -> dict[str, object]:
    self_coding = root / "artifacts" / "obtuse" / "blunder-flash-full-autonomy-compounding-bundle-v1" / "self-coding"
    plan = read_json(self_coding / "compound_evolution_plan.json") or {}
    planning = latest_json(
        root / "artifacts" / "obtuse" / "blunder-planning-coding-quality-gate-v1",
        "planning_coding_quality_gate.*.json",
    ) or {}
    status: dict[str, object]
    try:
        status = run_json_command(
            root,
            (
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(root / "scripts" / "obtuse" / "status-idle-ops.ps1"),
            ),
            45,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        status = {"available": False, "error": str(error)}
    safety = status.get("safetyState") if isinstance(status.get("safetyState"), dict) else {}
    return {
        "schema": "obtuse.blunder.shell.workspace.v3",
        "generatedAt": utc_now(),
        "workspace": {"name": "Blunder", "root": str(root), "private": True},
        "planning": {
            "score": planning.get("scorePercent"),
            "verdict": planning.get("verdict") or "NOT_RUN",
            "providersCalled": (planning.get("policy") or {}).get("providersCalled") if isinstance(planning.get("policy"), dict) else None,
            "launchesStarted": (planning.get("policy") or {}).get("launchesStarted") if isinstance(planning.get("policy"), dict) else None,
        },
        "evolution": {
            "status": plan.get("status") or status.get("compoundEvolutionPlanStatus") or "NOT_READY",
            "selectedFamily": plan.get("selectedFamily") or status.get("compoundEvolutionSelectedFamily"),
            "pressure": (plan.get("pressure") or {}).get("level") if isinstance(plan.get("pressure"), dict) else status.get("compoundEvolutionPressureLevel"),
            "candidateBudget": (plan.get("pressure") or {}).get("candidateBudgetNext") if isinstance(plan.get("pressure"), dict) else status.get("compoundEvolutionCandidateBudgetNext"),
            "championFitness": (plan.get("championFloor") or {}).get("fitness") if isinstance(plan.get("championFloor"), dict) else status.get("compoundEvolutionChampionFitness"),
            "cooledFamilies": plan.get("cooledDownFamilies") or status.get("compoundEvolutionCooledDownFamilies") or [],
            "rankedFamilies": plan.get("rankedFamilies") or [],
            "archive": plan.get("qualityDiversityArchive") or [],
            "advisoryOnly": (plan.get("safety") or {}).get("advisoryOnly", True) if isinstance(plan.get("safety"), dict) else True,
        },
        "queue": queue_summary(root),
        "provider": {
            "model": status.get("openRouterModelResolved") or "openrouter/z-ai/glm-5.2",
            "calls": status.get("providerCallsAttempted"),
            "failures": status.get("providerCallsFailed"),
            "enabled": status.get("openRouterIdleEnabled"),
        },
        "runtime": {
            "active": status.get("active"),
            "pid": status.get("activePid"),
            "pidValid": status.get("pidValid"),
            "heartbeatAgeSeconds": status.get("heartbeatAgeSeconds"),
            "lastAction": status.get("finalRecommendedOperatorAction"),
        },
        "safety": {
            "benchmarkLaunchAllowed": False,
            "destructiveActionsAllowed": False,
            "sourceMutationByPlanner": False,
            "providerCallsAllowed": safety.get("providerCallsAllowed", False),
            "secretsPrinted": False,
        },
        "actions": [asdict(spec) for spec in ACTION_SPECS],
        "capabilities": {
            "taskBriefing": True,
            "persistentMissionHistory": True,
            "hashGuardedCheckpoints": True,
            "deepSourceSearch": True,
            "liveAutonomySession": True,
        },
    }


def validate_task(task: str) -> str:
    normalized = task.strip()
    if len(normalized) < 3:
        raise ValueError("Describe a concrete task in at least three characters.")
    if len(normalized) > 4000:
        raise ValueError("Task exceeds the 4,000-character shell limit.")
    for label, pattern in TASK_BLOCK_PATTERNS:
        if pattern.search(normalized):
            raise ValueError(f"This shell does not authorize {label}; use an explicitly approved operational workflow.")
    return normalized


def build_task_briefing(root: Path, task: str) -> dict[str, object]:
    return task_briefing(root, validate_task(task))


def search_source_content(root: Path, query: str, limit: int) -> list[dict[str, object]]:
    return workspace_search(root, query, limit)


def action_command(root: Path, action_id: str, payload: Mapping[str, object]) -> tuple[list[str], int]:
    specs = {spec.action_id: spec for spec in ACTION_SPECS}
    if action_id not in specs:
        raise ValueError(f"Unknown shell action: {action_id}")
    spec = specs[action_id]
    if action_id == "status":
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "scripts" / "obtuse" / "status-idle-ops.ps1"),
        ], spec.timeout_seconds
    if action_id == "planning_gate":
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "scripts" / "obtuse" / "blunder.ps1"),
            "-Command",
            "PlanningCodingGate",
        ], spec.timeout_seconds
    if action_id == "evolve":
        ledger_root = root / "artifacts" / "obtuse" / "blunder-flash-full-autonomy-compounding-bundle-v1" / "self-coding"
        return [
            "python",
            str(root / "scripts" / "obtuse" / "blunder-compound-evolution-planner.py"),
            "--ledger-root",
            str(ledger_root),
            "--plan-path",
            str(ledger_root / "compound_evolution_plan.json"),
        ], spec.timeout_seconds
    if action_id == "tests":
        return ["python", "-m", "unittest", "discover", "-s", "tests", "-v"], spec.timeout_seconds
    if action_id == "readiness":
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "scripts" / "obtuse" / "blunder-s-tier-capability-audit.ps1"),
            "-Mode",
            "Validate",
        ], spec.timeout_seconds
    if payload.get("providerApproved") is not True:
        raise ValueError("Provider-backed tasks require the explicit approval switch in this shell.")
    task = validate_task(str(payload.get("task") or ""))
    return [
        "python",
        str(root / "scripts" / "obtuse" / "blunder_native_agent.py"),
        "--root",
        str(root),
        "--task",
        task,
        "--provider-approved",
    ], spec.timeout_seconds


def safe_file_path(root: Path, relative: str) -> Path:
    normalized = relative.replace("\\", "/").strip("/")
    if normalized in BLOCKED_EXACT_FILES:
        raise ValueError("That local-only file is not visible in Blunder Shell.")
    candidate = root / normalized
    resolved_candidate = candidate.resolve()
    resolved_root = root.resolve()
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError("File path escapes the Blunder workspace.")
    relative_parts = set(resolved_candidate.relative_to(resolved_root).parts)
    if relative_parts.intersection(BLOCKED_FILE_PARTS):
        raise ValueError("Generated, dependency, or private runtime paths are hidden.")
    if candidate.suffix.lower() not in TEXT_SUFFIXES:
        raise ValueError("Only text source files can be inspected in Blunder Shell.")
    if not candidate.is_file():
        raise ValueError("File does not exist.")
    if candidate.stat().st_size > 512 * 1024:
        raise ValueError("File exceeds the 512 KiB shell inspection limit.")
    return candidate


def search_files(root: Path, query: str, limit: int) -> list[dict[str, object]]:
    normalized = query.strip().lower()
    if len(normalized) < 2:
        return []
    command = [
            "rg",
            "--files",
            str(root),
            "-g",
            "!artifacts/**",
            "-g",
            "!**/node_modules/**",
            "-g",
            "!**/__pycache__/**",
        ]
    record_process_boundary(
        root,
        "authenticated-shell",
        "source-file-search",
        command,
        None,
        datetime.now(timezone.utc),
    )
    completed = subprocess.run(
        command,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
        creationflags=process_flags(),
    )
    if completed.returncode not in (0, 1):
        raise RuntimeError(f"file search failed: {completed.stderr[-500:]}")
    results: list[dict[str, object]] = []
    for value in completed.stdout.splitlines():
        path = Path(value)
        relative = path.relative_to(root).as_posix() if path.is_absolute() else path.as_posix()
        if normalized not in relative.lower():
            continue
        results.append({"path": relative, "name": Path(relative).name})
        if len(results) >= limit:
            break
    return results


def read_source_file(root: Path, relative: str) -> dict[str, object]:
    path = safe_file_path(root, relative)
    text = path.read_text(encoding="utf-8", errors="replace")
    normalized = path.relative_to(root).as_posix()
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest().upper()
    return {
        "path": normalized,
        "content": text,
        "bytes": path.stat().st_size,
        "sha256": digest,
        "saveConfirmation": f"SAVE {normalized} {digest[:12]}",
        "analysis": document_analysis(normalized, text),
    }


def analyze_source_file(root: Path, relative: str, content: str | None) -> dict[str, object]:
    path = safe_file_path(root, relative)
    value = path.read_text(encoding="utf-8", errors="replace") if content is None else content
    if len(value.encode("utf-8")) > 512 * 1024:
        raise ValueError("Editor analysis content exceeds 512 KiB.")
    return document_analysis(path.relative_to(root).as_posix(), value)


def definition_locations(root: Path, symbol: str) -> dict[str, object]:
    return {"symbol": symbol.strip(), "locations": symbol_definitions(root, symbol, 40)}


def save_source_file(
    root: Path,
    relative: str,
    content: str,
    expected_sha256: str,
    confirmation: str,
) -> dict[str, object]:
    path = safe_file_path(root, relative)
    return save_document(root, path, content, expected_sha256, confirmation)


def redact_text(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("<redacted-secret>", redacted)
    return redacted


def sanitized_environment() -> dict[str, str]:
    return {str(key): str(value) for key, value in os.environ.items()}
