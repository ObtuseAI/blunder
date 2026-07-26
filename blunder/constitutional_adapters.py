"""Shadow-only adapters between current Blunder gates and constitutional policy."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence

from blunder.cognitive_events import (
    fail_closed_summary,
    record_fail_closed_decision,
    record_shadow_decision,
    shadow_summary,
)
from blunder.constitutional_kernel import (
    AuthorityEnvelope,
    CausalIdentity,
    build_authority_envelope,
    build_causal_identity,
    canonical_sha256,
    load_runtime_constitution,
)


_REQUESTED_DENIAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bgit\s+push\b|\bpush\b.{0,30}\b(?:branch|commit|repository|remote)\b", re.IGNORECASE),
        "git_push",
    ),
    (
        re.compile(
            r"\b(?:publish|submit|release)\b.{0,40}\b(?:public|publicly|submission|app store)\b|"
            r"\b(?:public|publicly)\b.{0,40}\b(?:publish|submit|release)\b",
            re.IGNORECASE,
        ),
        "public_submission",
    ),
    (
        re.compile(r"\bdeploy\b.{0,30}\bproduction\b|\bproduction\b.{0,30}\bdeploy\b", re.IGNORECASE),
        "production_deploy",
    ),
    (
        re.compile(
            r"\b(?:print|show|expose|reveal|log|dump|export)\b.{0,40}"
            r"\b(?:credential|secret|api key|access token|auth token)\b",
            re.IGNORECASE,
        ),
        "secret_printing",
    ),
    (
        re.compile(
            r"\b(?:launch|start|resume|run|upload|publish)\b.{0,40}\b(?:live\s+)?benchmark\b|"
            r"\b(?:live\s+)?benchmark\b.{0,40}\b(?:launch|start|resume|run|upload|publish)\b",
            re.IGNORECASE,
        ),
        "live_benchmark_launch",
    ),
    (re.compile(r"\b(?:delete|wipe|remove recursively|reset --hard)\b", re.IGNORECASE), "destructive_filesystem_cleanup"),
    (
        re.compile(r"\b(?:disable|weaken|bypass)\b.{0,30}\b(?:safety|validation)\b", re.IGNORECASE),
        "safety_boundary_weakening",
    ),
    (
        re.compile(r"\b(?:disable|weaken|bypass)\b.{0,30}\b(?:permission|acl)\b", re.IGNORECASE),
        "windows_permission_or_acl_bypass",
    ),
)


def requested_hard_denials(task: str) -> tuple[str, ...]:
    """Map a task to the exact immutable denial identifiers it requests."""

    return tuple(sorted({denial for pattern, denial in _REQUESTED_DENIAL_PATTERNS if pattern.search(task)}))


def requested_command_denials(command: Sequence[str]) -> tuple[str, ...]:
    """Recognize exact prohibited command operations without interpreting flag names as intent."""

    normalized = tuple(part.strip().lower() for part in command)
    executable = Path(normalized[0]).name if normalized else ""
    if executable in {"git", "git.exe"} and "push" in normalized[1:]:
        return ("git_push",)
    return ()


def _authority_id(prefix: str, source: str, nonce: str) -> str:
    return f"{prefix}:{canonical_sha256([source, nonce])[:32]}"


def _identity(actor_id: str, mission_id: str | None, nonce: str) -> CausalIdentity:
    constitution = load_runtime_constitution()
    return build_causal_identity(
        actor_id,
        constitution.policy_epoch,
        constitution.source_revision,
        mission_id,
        None,
        nonce,
    )


def _effect_contract(
    effect: str,
    actor_id: str,
    subject_id: str,
    authority_source: str,
    scopes: Sequence[str],
    approvals: Sequence[str],
    requested_capabilities: Sequence[str],
    mission_id: str | None,
    observed_at: datetime,
) -> tuple[AuthorityEnvelope, CausalIdentity]:
    nonce = uuid.uuid4().hex
    constitution = load_runtime_constitution()
    envelope = build_authority_envelope(
        _authority_id(effect, authority_source, nonce),
        actor_id,
        subject_id,
        effect,
        scopes,
        approvals,
        constitution.immutable_denials,
        requested_capabilities,
        observed_at + timedelta(minutes=5),
    )
    return envelope, _identity(actor_id, mission_id, nonce)


def _record_fail_closed_effect(
    root: Path,
    effect: str,
    actor_id: str,
    subject_id: str,
    authority_source: str,
    scopes: Sequence[str],
    approvals: Sequence[str],
    requested_capabilities: Sequence[str],
    mission_id: str | None,
    current_allowed: bool,
    current_reasons: Sequence[str],
    observed_at: datetime,
) -> dict[str, object]:
    envelope, identity = _effect_contract(
        effect,
        actor_id,
        subject_id,
        authority_source,
        scopes,
        approvals,
        requested_capabilities,
        mission_id,
        observed_at,
    )
    event = record_fail_closed_decision(
        root,
        envelope,
        identity,
        current_allowed,
        current_reasons,
        observed_at,
    )
    return fail_closed_summary(event)


def record_self_coding_shadow(
    root: Path,
    authorization: Mapping[str, object],
    task: str,
    gates: Mapping[str, bool],
    scopes: Sequence[str],
    preserved_denials: Sequence[str],
    expires_at: datetime | None,
    observed_at: datetime,
) -> dict[str, object]:
    """Record the current bounded-self-coding preflight as a shadow decision."""

    nonce = uuid.uuid4().hex
    approval_gates = (
        "schemaValid",
        "explicitlyEnabled",
        "authorizationIdentified",
        "operatorStatementPresent",
    )
    approvals = ("operator-authorization",) if all(gates.get(name) is True for name in approval_gates) else ()
    effective_expiry = observed_at if expires_at is None else expires_at
    envelope = build_authority_envelope(
        _authority_id("self-coding", str(authorization.get("authorizationId") or "missing"), nonce),
        "local-operator",
        "self-coding-request",
        "self-coding.launch",
        scopes,
        approvals,
        preserved_denials,
        requested_hard_denials(task),
        effective_expiry,
    )
    failed_gates = tuple(sorted(name for name, passed in gates.items() if not passed))
    event = record_shadow_decision(
        root,
        envelope,
        _identity("local-operator", None, nonce),
        all(gates.values()),
        failed_gates,
        observed_at,
    )
    return shadow_summary(event)


def record_provider_boundary(root: Path, provider_approved: bool, observed_at: datetime) -> dict[str, object]:
    """Require constitutional agreement at the external provider boundary."""

    return _record_fail_closed_effect(
        root,
        "provider.call",
        "native-agent",
        "model-provider",
        "native-agent",
        ("provider:model-use",),
        ("operator-provider-approval",) if provider_approved else (),
        (),
        None,
        provider_approved,
        () if provider_approved else ("providerApprovalMissing",),
        observed_at,
    )


def record_source_mutation_boundary(
    root: Path,
    review: Mapping[str, object],
    intent_court: Mapping[str, object],
    preflight_court: Mapping[str, object],
    observed_at: datetime,
) -> dict[str, object]:
    """Require constitutional agreement immediately before source mutation."""

    approvals = tuple(
        name
        for name, result in (
            ("intent-court", intent_court),
            ("verification-court", preflight_court),
            ("independent-review", review),
        )
        if result.get("verdict") == "APPROVE"
    )
    current_allowed = len(approvals) == 3
    return _record_fail_closed_effect(
        root,
        "source.mutate",
        "native-agent",
        "bounded-proposal",
        "native-agent",
        ("source:write-bounded",),
        approvals,
        (),
        None,
        current_allowed,
        () if current_allowed else ("courtOrReviewRejected",),
        observed_at,
    )


def record_guarded_source_write_boundary(
    root: Path,
    actor_id: str,
    subject_id: str,
    authority_source: str,
    observed_at: datetime,
) -> dict[str, object]:
    """Require agreement after a bounded source writer's local gates pass."""

    return _record_fail_closed_effect(
        root,
        "source.mutate",
        actor_id,
        subject_id,
        authority_source,
        ("source:write-bounded",),
        ("intent-court", "verification-court", "independent-review"),
        (),
        None,
        True,
        (),
        observed_at,
    )
def record_mission_integration_boundary(
    root: Path,
    mission_id: str,
    completed: bool,
    exact_confirmation: bool,
    validated_patch: bool,
    observed_at: datetime,
) -> dict[str, object]:
    """Require constitutional agreement at mission integration."""

    approvals = tuple(
        name
        for name, passed in (
            ("completed-mission", completed),
            ("exact-confirmation", exact_confirmation),
            ("validated-patch", validated_patch),
        )
        if passed
    )
    current_allowed = completed and exact_confirmation and validated_patch
    failed = tuple(
        name
        for name, passed in (
            ("completedMission", completed),
            ("exactConfirmation", exact_confirmation),
            ("validatedPatch", validated_patch),
        )
        if not passed
    )
    return _record_fail_closed_effect(
        root,
        "mission.integrate",
        "mission-branch-manager",
        "mission-integration",
        mission_id,
        ("mission:integrate",),
        approvals,
        (),
        mission_id,
        current_allowed,
        failed,
        observed_at,
    )


def record_process_boundary(
    root: Path,
    actor_id: str,
    subject_id: str,
    command: Sequence[str],
    mission_id: str | None,
    observed_at: datetime,
) -> dict[str, object]:
    """Require agreement before a bounded subprocess starts."""

    return _record_fail_closed_effect(
        root,
        "subprocess.start",
        actor_id,
        subject_id,
        canonical_sha256(list(command)),
        ("process:start-bounded",),
        (),
        requested_command_denials(command),
        mission_id,
        True,
        (),
        observed_at,
    )


def record_validation_boundaries(
    root: Path,
    command: Sequence[str],
    observed_at: datetime,
) -> dict[str, object]:
    """Report process authority and the validation sandbox's network denial."""

    process = record_process_boundary(
        root,
        "validation-broker",
        "validation-shard",
        command,
        None,
        observed_at,
    )
    network = _record_fail_closed_effect(
        root,
        "network.external",
        "validation-broker",
        "validation-shard",
        canonical_sha256(list(command)),
        (),
        (),
        (),
        None,
        False,
        ("externalNetworkBlockedByValidationPolicy",),
        observed_at,
    )
    return {"process": process, "externalNetwork": network}


def record_worktree_creation_boundary(
    root: Path,
    mission_id: str,
    task: str,
    observed_at: datetime,
) -> dict[str, object]:
    """Require agreement before creating an isolated mission worktree."""

    return _record_fail_closed_effect(
        root,
        "worktree.create",
        "mission-branch-manager",
        "mission-worktree",
        mission_id,
        ("worktree:create",),
        (),
        requested_hard_denials(task),
        mission_id,
        True,
        (),
        observed_at,
    )


def record_mission_cleanup_boundary(
    root: Path,
    mission_id: str,
    terminal: bool,
    exact_confirmation: bool,
    paths_contained: bool,
    observed_at: datetime,
) -> dict[str, object]:
    """Require agreement before bounded worktree and branch cleanup."""

    current_allowed = terminal and exact_confirmation and paths_contained
    reasons = tuple(
        name
        for name, passed in (
            ("terminalMission", terminal),
            ("exactConfirmation", exact_confirmation),
            ("pathsContained", paths_contained),
        )
        if not passed
    )
    return _record_fail_closed_effect(
        root,
        "mission.cleanup",
        "mission-branch-manager",
        "mission-worktree",
        mission_id,
        ("mission:cleanup",) if paths_contained else (),
        ("operator-cleanup-approval",) if exact_confirmation else (),
        (),
        mission_id,
        current_allowed,
        reasons,
        observed_at,
    )


def record_shell_action_boundary(
    root: Path,
    action_id: str,
    command: Sequence[str],
    task: str,
    observed_at: datetime,
) -> dict[str, object]:
    """Require agreement before authenticated shell action dispatch."""

    requested = tuple(sorted(set(requested_hard_denials(task)) | set(requested_command_denials(command))))
    return _record_fail_closed_effect(
        root,
        "subprocess.start",
        "authenticated-shell",
        "shell-action",
        action_id,
        ("process:start-bounded",),
        (),
        requested,
        None,
        True,
        (),
        observed_at,
    )


def record_requested_denial_boundaries(
    root: Path,
    actor_id: str,
    task: str,
    observed_at: datetime,
) -> dict[str, object]:
    """Emit one canonical denial for every immutable capability a task requests."""

    effect_by_denial = {
        "windows_permission_or_acl_bypass": "permission.bypass",
        "secret_printing": "credentials.reveal",
        "live_benchmark_launch": "benchmark.live-launch",
        "destructive_filesystem_cleanup": "filesystem.destructive-cleanup",
        "git_push": "git.push",
        "safety_boundary_weakening": "policy.root-mutate",
        "production_deploy": "deployment.production",
        "public_submission": "release.publish",
    }
    decisions: dict[str, object] = {}
    for denial in requested_hard_denials(task):
        effect = effect_by_denial[denial]
        decisions[denial] = _record_fail_closed_effect(
            root,
            effect,
            actor_id,
            "requested-immutable-effect",
            canonical_sha256([actor_id, denial, task]),
            (),
            (),
            (denial,),
            None,
            False,
            ("currentHardDenial",),
            observed_at,
        )
    return decisions


def record_release_governance_boundaries(root: Path, observed_at: datetime) -> dict[str, object]:
    """Emit the release manifest's closed commit, push, publish, and deploy authority."""

    decisions: dict[str, object] = {}
    for effect, requested in (
        ("git.commit", ()),
        ("git.push", ("git_push",)),
        ("release.publish", ("public_submission",)),
        ("deployment.production", ("production_deploy",)),
    ):
        decisions[effect] = _record_fail_closed_effect(
            root,
            effect,
            "release-governance",
            "private-release-manifest",
            effect,
            (),
            (),
            requested,
            None,
            False,
            ("exactOperatorConfirmationAbsent",),
            observed_at,
        )
    return decisions


def record_root_policy_activation_denial(
    root: Path,
    proposal_id: str,
    observed_at: datetime,
) -> dict[str, object]:
    """Record that an amendment proposal carries no activation authority."""

    return _record_fail_closed_effect(
        root,
        "policy.root-mutate",
        "constitutional-amendment-author",
        "root-constitution",
        proposal_id,
        (),
        (),
        ("safety_boundary_weakening",),
        None,
        False,
        ("proposalCannotActivate",),
        observed_at,
    )
