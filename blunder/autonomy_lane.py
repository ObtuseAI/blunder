from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from blunder.constitutional_adapters import record_process_boundary, record_self_coding_shadow
from blunder.native_agent import validate_change_path


AUTHORIZATION_SCHEMA = "obtuse.blunder.operator_autonomy_authorization.v1"
SELF_CODING_SCOPE = "bounded_self_coding"
REQUIRED_HARD_DENIALS: frozenset[str] = frozenset(
    {
        "windows_permission_or_acl_bypass",
        "secret_printing",
        "live_benchmark_launch",
        "destructive_filesystem_cleanup",
        "git_push",
        "safety_boundary_weakening",
        "production_deploy",
        "public_submission",
    }
)
HARD_DENIAL_TERMS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:push|publish|deploy|release)\b", re.IGNORECASE),
    re.compile(r"\b(?:credential|secret|api key|token)\b", re.IGNORECASE),
    re.compile(r"\b(?:benchmark|production|public submission)\b", re.IGNORECASE),
    re.compile(r"\b(?:delete|wipe|remove recursively|reset --hard)\b", re.IGNORECASE),
    re.compile(r"\b(?:disable|weaken|bypass)\b.{0,30}\b(?:safety|validation|permission)\b", re.IGNORECASE),
)


class ClaimedMissionStarter(Protocol):
    def start_claimed(
        self,
        task: str,
        provider_approved: bool,
        claimed_paths: Sequence[str],
        program_id: str | None,
        role: str,
    ) -> dict[str, object]: ...


@dataclass(frozen=True)
class SelfCodingBudget:
    seconds: int
    provider_calls: int
    tokens: int
    cost_usd: float


@dataclass(frozen=True)
class SelfCodingRequest:
    task: str
    target_paths: tuple[str, ...]
    budget: SelfCodingBudget


def read_authorization(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read self-coding authorization {path}: {error}") from error
    if not isinstance(value, dict) or value.get("schema") != AUTHORIZATION_SCHEMA:
        raise ValueError(f"Self-coding authorization has an unsupported contract: {path}")
    return value


def git_clean(root: Path) -> bool:
    command = ("git", "-C", str(root), "status", "--porcelain")
    record_process_boundary(
        root,
        "self-coding-preflight",
        "git-status-probe",
        command,
        None,
        datetime.now(timezone.utc),
    )
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Cannot inspect self-coding worktree: {completed.stderr[-500:]}")
    return not completed.stdout.strip()


def parse_expiry(value: object) -> datetime | None:
    if value is None:
        return None
    observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return observed if observed.tzinfo is not None else observed.replace(tzinfo=timezone.utc)


def self_coding_preflight(
    root: Path,
    authorization: Mapping[str, object],
    request: SelfCodingRequest,
    require_clean_tree: bool,
) -> dict[str, object]:
    observed_at = datetime.now(timezone.utc)
    approved_scopes = authorization.get("approvedScopes")
    scopes = frozenset(str(item) for item in approved_scopes) if isinstance(approved_scopes, list) else frozenset()
    preserved = authorization.get("preserveHardDenials")
    hard_denials = tuple(str(item) for item in preserved) if isinstance(preserved, list) else ()
    expiry = parse_expiry(authorization.get("expiresAt"))
    target_errors: list[str] = []
    for relative in request.target_paths:
        try:
            validate_change_path(root, relative)
        except ValueError as error:
            target_errors.append(str(error))
    denied_task = next((pattern.pattern for pattern in HARD_DENIAL_TERMS if pattern.search(request.task)), None)
    gates = {
        "schemaValid": authorization.get("schema") == AUTHORIZATION_SCHEMA,
        "explicitlyEnabled": authorization.get("enabled") is True,
        "authorizationIdentified": str(authorization.get("authorizationId") or "") not in {"", "local-operator-authorization-required"},
        "operatorStatementPresent": bool(str(authorization.get("operatorStatement") or "").strip()),
        "scopeApproved": SELF_CODING_SCOPE in scopes,
        "authorizationUnexpired": expiry is not None and expiry > observed_at,
        "hardDenialsPreserved": REQUIRED_HARD_DENIALS.issubset(hard_denials),
        "taskAllowed": denied_task is None,
        "oneTargetOnly": len(request.target_paths) == 1,
        "targetValid": not target_errors,
        "budgetBounded": (
            60 <= request.budget.seconds <= 900
            and 1 <= request.budget.provider_calls <= 3
            and 1_000 <= request.budget.tokens <= 60_000
            and 0 < request.budget.cost_usd <= 0.25
        ),
        "reviewTreeClean": git_clean(root) if require_clean_tree else True,
    }
    constitutional_shadow = record_self_coding_shadow(
        root,
        authorization,
        request.task,
        gates,
        tuple(scopes),
        hard_denials,
        expiry,
        observed_at,
    )
    return {
        "schema": "obtuse.blunder.bounded-self-coding-preflight.v1",
        "status": "AUTHORIZED_TO_LAUNCH_ISOLATED_MISSION" if all(gates.values()) else "BLOCKED",
        "gates": gates,
        "authorizationId": authorization.get("authorizationId"),
        "request": {"task": request.task, "targetPaths": list(request.target_paths), "budget": asdict(request.budget)},
        "targetErrors": target_errors,
        "deniedTaskPattern": denied_task,
        "integrationAuthorized": False,
        "commitAuthorized": False,
        "pushAuthorized": False,
        "permissionExpansion": False,
        "constitutionalShadow": constitutional_shadow,
    }


def launch_self_coding_mission(
    starter: ClaimedMissionStarter,
    request: SelfCodingRequest,
    preflight: Mapping[str, object],
) -> dict[str, object]:
    if preflight.get("status") != "AUTHORIZED_TO_LAUNCH_ISOLATED_MISSION":
        raise PermissionError("Self-coding preflight is not authorized.")
    return starter.start_claimed(request.task, True, request.target_paths, None, "self-coding")
