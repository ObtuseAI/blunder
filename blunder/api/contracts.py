from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Mapping, Sequence


IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,95}$")
DIGEST_PATTERN = re.compile(r"^[A-F0-9]{64}$")
MAX_TEXT = 4_000
MAX_LIST = 64


class TaskStageV1(StrEnum):
    UNDERSTANDING = "UNDERSTANDING"
    EXPLORING = "EXPLORING"
    DESIGNING = "DESIGNING"
    BUILDING = "BUILDING"
    VERIFYING = "VERIFYING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"


class TaskStateV1(StrEnum):
    UNDERSTANDING = "UNDERSTANDING"
    EXPLORING = "EXPLORING"
    DESIGNING = "DESIGNING"
    BUILDING = "BUILDING"
    VERIFYING = "VERIFYING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    BLOCKED = "BLOCKED"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    ABSTAINED = "ABSTAINED"
    CANCELLED = "CANCELLED"


TERMINAL_TASK_STATES: frozenset[TaskStateV1] = frozenset(
    {TaskStateV1.ABSTAINED, TaskStateV1.CANCELLED}
)


@dataclass(frozen=True)
class TaskBudgetV1:
    maximum_seconds: int
    maximum_provider_calls: int
    maximum_tokens: int
    maximum_cost_usd: float


@dataclass(frozen=True)
class TaskAuthorityV1:
    repository_read: bool
    research: bool
    prototype: bool
    isolated_source_mutation: bool
    provider: bool
    network: bool
    apply_to_review_tree: bool
    integrate: bool
    commit: bool
    push: bool
    deploy: bool


@dataclass(frozen=True)
class ProviderPolicyV1:
    enabled: bool
    allowed_providers: tuple[str, ...]
    allowed_data_classes: tuple[str, ...]
    maximum_cost_per_task_usd: float
    maximum_cost_per_day_usd: float
    maximum_calls_per_task: int
    credential_reference: str | None


@dataclass(frozen=True)
class ProjectPolicyV1:
    schema: str
    project_id: str
    allowed_roots: tuple[str, ...]
    protected_paths: tuple[str, ...]
    allowed_commands: tuple[tuple[str, ...], ...]
    provider: ProviderPolicyV1
    preview_allowed: bool
    external_deployment_allowed: bool


@dataclass(frozen=True)
class TaskContractV1:
    schema: str
    task_id: str
    project_id: str
    objective: str
    constraints: tuple[str, ...]
    success_criteria: tuple[str, ...]
    deliverables: tuple[str, ...]
    assumptions: tuple[str, ...]
    unknowns: tuple[str, ...]
    authority: TaskAuthorityV1
    budget: TaskBudgetV1
    source_revision: str
    created_at: str
    contract_digest: str


@dataclass(frozen=True)
class ArtifactRefV1:
    artifact_id: str
    task_id: str
    kind: str
    relative_path: str
    sha256: str
    bytes: int
    created_at: str


@dataclass(frozen=True)
class ApprovalRequestV1:
    approval_id: str
    task_id: str
    kind: str
    reason: str
    exact_approve_phrase: str
    exact_deny_phrase: str
    status: str
    created_at: str
    decided_at: str | None


@dataclass(frozen=True)
class TaskSnapshotV1:
    schema: str
    contract: TaskContractV1
    stage: TaskStageV1
    state: TaskStateV1
    status_reason: str
    selected_agents: tuple[str, ...]
    artifacts: tuple[ArtifactRefV1, ...]
    approvals: tuple[ApprovalRequestV1, ...]
    blockers: tuple[str, ...]
    mission_id: str | None
    event_sequence: int
    event_digest: str
    updated_at: str


@dataclass(frozen=True)
class TaskEventV1:
    schema: str
    event_id: str
    task_id: str
    project_id: str
    sequence: int
    kind: str
    stage: TaskStageV1
    state: TaskStateV1
    causal_identity: str
    input_digests: tuple[str, ...]
    output_digests: tuple[str, ...]
    evidence_references: tuple[str, ...]
    resource_charge: Mapping[str, int | float | str]
    payload: Mapping[str, object]
    created_at: str
    previous_event_digest: str | None
    event_digest: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"Value cannot be encoded as canonical JSON: {error}") from error
    return encoded.encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def strict_json_loads(text: str, context: str) -> object:
    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"{context} contains a duplicate key: {key}")
            value[key] = item
        return value

    def non_finite(value: str) -> object:
        raise ValueError(f"{context} contains a non-finite number: {value}")

    try:
        return json.loads(text, object_pairs_hook=object_pairs, parse_constant=non_finite)
    except json.JSONDecodeError as error:
        raise ValueError(f"{context} is not valid JSON: {error}") from error


def require_object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object.")
    return dict(value)


def require_exact_keys(value: Mapping[str, object], expected: frozenset[str], context: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(f"{context} fields are invalid; missing={missing}, unknown={unknown}.")


def require_identifier(value: object, context: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{context} must be a lowercase hyphenated identifier between 3 and 96 characters.")
    return value


def require_text(value: object, context: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{context} must be text.")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{context} must contain between 1 and {maximum} characters.")
    return normalized


def optional_text(value: object, context: str, maximum: int) -> str | None:
    if value is None:
        return None
    return require_text(value, context, maximum)


def require_boolean(value: object, context: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{context} must be true or false.")
    return value


def require_integer(value: object, context: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise ValueError(f"{context} must be an integer between {minimum} and {maximum}.")
    return value


def require_number(value: object, context: str, minimum: float, maximum: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{context} must be a number.")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < minimum or parsed > maximum:
        raise ValueError(f"{context} must be finite and between {minimum} and {maximum}.")
    return parsed


def require_text_tuple(value: object, context: str, maximum_items: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ValueError(f"{context} must be a list with at most {maximum_items} entries.")
    parsed = tuple(require_text(item, f"{context}[{index}]", MAX_TEXT) for index, item in enumerate(value))
    if len(set(parsed)) != len(parsed):
        raise ValueError(f"{context} must not contain duplicate entries.")
    return parsed


def require_digest(value: object, context: str) -> str:
    if not isinstance(value, str) or not DIGEST_PATTERN.fullmatch(value.upper()):
        raise ValueError(f"{context} must be a 64-character SHA-256 digest.")
    return value.upper()


def parse_budget(value: object) -> TaskBudgetV1:
    raw = require_object(value, "Task budget")
    require_exact_keys(
        raw,
        frozenset({"maximumSeconds", "maximumProviderCalls", "maximumTokens", "maximumCostUsd"}),
        "Task budget",
    )
    return TaskBudgetV1(
        require_integer(raw["maximumSeconds"], "Task budget maximumSeconds", 1, 86_400),
        require_integer(raw["maximumProviderCalls"], "Task budget maximumProviderCalls", 0, 1_000),
        require_integer(raw["maximumTokens"], "Task budget maximumTokens", 0, 10_000_000),
        require_number(raw["maximumCostUsd"], "Task budget maximumCostUsd", 0.0, 100_000.0),
    )


def parse_authority(value: object) -> TaskAuthorityV1:
    raw = require_object(value, "Task authority")
    fields = frozenset(
        {
            "repositoryRead",
            "research",
            "prototype",
            "isolatedSourceMutation",
            "provider",
            "network",
            "applyToReviewTree",
            "integrate",
            "commit",
            "push",
            "deploy",
        }
    )
    require_exact_keys(raw, fields, "Task authority")
    parsed = TaskAuthorityV1(
        require_boolean(raw["repositoryRead"], "Task authority repositoryRead"),
        require_boolean(raw["research"], "Task authority research"),
        require_boolean(raw["prototype"], "Task authority prototype"),
        require_boolean(raw["isolatedSourceMutation"], "Task authority isolatedSourceMutation"),
        require_boolean(raw["provider"], "Task authority provider"),
        require_boolean(raw["network"], "Task authority network"),
        require_boolean(raw["applyToReviewTree"], "Task authority applyToReviewTree"),
        require_boolean(raw["integrate"], "Task authority integrate"),
        require_boolean(raw["commit"], "Task authority commit"),
        require_boolean(raw["push"], "Task authority push"),
        require_boolean(raw["deploy"], "Task authority deploy"),
    )
    if parsed.provider and not parsed.network:
        raise ValueError("Task authority cannot allow a provider while network authority is false.")
    if parsed.push and not parsed.commit:
        raise ValueError("Task authority cannot allow push while commit authority is false.")
    if parsed.deploy and not parsed.network:
        raise ValueError("Task authority cannot allow deployment while network authority is false.")
    return parsed


def task_contract_payload(contract: TaskContractV1) -> dict[str, object]:
    value = asdict(contract)
    value.pop("contract_digest", None)
    return value


def build_task_contract(
    task_id: str,
    project_id: str,
    objective: str,
    constraints: Sequence[str],
    success_criteria: Sequence[str],
    deliverables: Sequence[str],
    assumptions: Sequence[str],
    unknowns: Sequence[str],
    authority: TaskAuthorityV1,
    budget: TaskBudgetV1,
    source_revision: str,
    created_at: str,
) -> TaskContractV1:
    base = TaskContractV1(
        "obtuse.blunder.task-contract.v1",
        require_identifier(task_id, "Task id"),
        require_identifier(project_id, "Project id"),
        require_text(objective, "Task objective", MAX_TEXT),
        tuple(constraints),
        tuple(success_criteria),
        tuple(deliverables),
        tuple(assumptions),
        tuple(unknowns),
        authority,
        budget,
        require_text(source_revision, "Task source revision", 200),
        require_text(created_at, "Task createdAt", 100),
        "",
    )
    return replace(base, contract_digest=canonical_sha256(task_contract_payload(base)))


def parse_provider_policy(value: object) -> ProviderPolicyV1:
    raw = require_object(value, "Provider policy")
    require_exact_keys(
        raw,
        frozenset(
            {
                "enabled",
                "allowedProviders",
                "allowedDataClasses",
                "maximumCostPerTaskUsd",
                "maximumCostPerDayUsd",
                "maximumCallsPerTask",
                "credentialReference",
            }
        ),
        "Provider policy",
    )
    policy = ProviderPolicyV1(
        require_boolean(raw["enabled"], "Provider policy enabled"),
        require_text_tuple(raw["allowedProviders"], "Provider policy allowedProviders", 32),
        require_text_tuple(raw["allowedDataClasses"], "Provider policy allowedDataClasses", 32),
        require_number(raw["maximumCostPerTaskUsd"], "Provider policy maximumCostPerTaskUsd", 0.0, 100_000.0),
        require_number(raw["maximumCostPerDayUsd"], "Provider policy maximumCostPerDayUsd", 0.0, 1_000_000.0),
        require_integer(raw["maximumCallsPerTask"], "Provider policy maximumCallsPerTask", 0, 1_000),
        optional_text(raw["credentialReference"], "Provider policy credentialReference", 256),
    )
    if policy.enabled and (not policy.allowed_providers or policy.maximum_calls_per_task < 1 or policy.credential_reference is None):
        raise ValueError("Enabled provider policy requires providers, at least one call, and an opaque credential reference.")
    if not policy.enabled and (
        policy.allowed_providers
        or policy.maximum_calls_per_task != 0
        or policy.maximum_cost_per_task_usd != 0.0
        or policy.maximum_cost_per_day_usd != 0.0
        or policy.credential_reference is not None
    ):
        raise ValueError("Disabled provider policy must not retain provider authority or a credential reference.")
    return policy


def load_project_policy(repository_root: Path, path: Path) -> ProjectPolicyV1:
    raw = require_object(strict_json_loads(path.read_text(encoding="utf-8"), "Project policy"), "Project policy")
    require_exact_keys(
        raw,
        frozenset(
            {
                "schema",
                "projectId",
                "allowedRoots",
                "protectedPaths",
                "allowedCommands",
                "provider",
                "previewAllowed",
                "externalDeploymentAllowed",
            }
        ),
        "Project policy",
    )
    if raw["schema"] != "obtuse.blunder.project-policy.v1":
        raise ValueError("Project policy schema must be obtuse.blunder.project-policy.v1.")
    roots = require_text_tuple(raw["allowedRoots"], "Project policy allowedRoots", 16)
    resolved_roots: list[str] = []
    for root in roots:
        resolved = (repository_root / root).resolve() if not Path(root).is_absolute() else Path(root).resolve()
        if not resolved.is_relative_to(repository_root.resolve()):
            raise ValueError(f"Project policy allowed root escapes the repository: {root}")
        resolved_roots.append(str(resolved))
    commands_raw = raw["allowedCommands"]
    if not isinstance(commands_raw, list) or len(commands_raw) > 32:
        raise ValueError("Project policy allowedCommands must be a list with at most 32 entries.")
    commands: list[tuple[str, ...]] = []
    for index, command in enumerate(commands_raw):
        if not isinstance(command, list) or not command or len(command) > 32:
            raise ValueError(f"Project policy allowedCommands[{index}] must be a non-empty bounded list.")
        commands.append(tuple(require_text(item, f"Project policy allowedCommands[{index}] item", 512) for item in command))
    deployment = require_boolean(raw["externalDeploymentAllowed"], "Project policy externalDeploymentAllowed")
    provider = parse_provider_policy(raw["provider"])
    if deployment:
        raise ValueError("Blunder vNext foundation does not accept external deployment authority.")
    return ProjectPolicyV1(
        str(raw["schema"]),
        require_identifier(raw["projectId"], "Project policy projectId"),
        tuple(resolved_roots),
        require_text_tuple(raw["protectedPaths"], "Project policy protectedPaths", 64),
        tuple(commands),
        provider,
        require_boolean(raw["previewAllowed"], "Project policy previewAllowed"),
        deployment,
    )


def public_value(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {camel_case(field.name): public_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): public_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [public_value(item) for item in value]
    return value


def camel_case(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)
