from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from blunder.api.contracts import ProjectPolicyV1, TaskContractV1, TaskStageV1, TaskStateV1
from blunder.objectives import ObjectiveInterpretationV1


@dataclass(frozen=True)
class SupervisorDecisionV1:
    schema: str
    stage: TaskStageV1
    state: TaskStateV1
    strategy: str
    selected_roles: tuple[str, ...]
    required_evidence: tuple[str, ...]
    stopping_conditions: tuple[str, ...]
    approval_kind: str | None
    reasons: tuple[str, ...]


def selected_roles(interpretation: ObjectiveInterpretationV1) -> tuple[str, ...]:
    roles: list[str] = ["problem-framing", "project-analysis"]
    if interpretation.research_requested:
        roles.extend(("research", "evidence-critic"))
    if interpretation.prototype_requested:
        roles.extend(("product-strategy", "ux-design", "visual-review"))
    if interpretation.source_mutation_requested:
        roles.extend(("builder", "code-review", "validator"))
    roles.append("historian")
    return tuple(dict.fromkeys(roles))


def supervise_task(
    contract: TaskContractV1,
    interpretation: ObjectiveInterpretationV1,
    policy: ProjectPolicyV1,
    capability_route: Mapping[str, object],
) -> SupervisorDecisionV1:
    reasons: list[str] = []
    approval_kind: str | None = None
    if interpretation.external_effect_requested:
        stage = TaskStageV1.UNDERSTANDING
        state = TaskStateV1.BLOCKED
        strategy = "authority-boundary-review"
        reasons.append("External effects are outside the configured project authority.")
    elif interpretation.clarification_required:
        stage = TaskStageV1.UNDERSTANDING
        state = TaskStateV1.BLOCKED
        strategy = "clarify-material-ambiguity"
        reasons.extend(interpretation.clarification_reasons)
    elif (interpretation.source_mutation_requested or interpretation.research_requested) and not contract.authority.provider:
        stage = TaskStageV1.UNDERSTANDING
        state = TaskStateV1.NEEDS_APPROVAL
        strategy = "provider-authority-review"
        approval_kind = "provider-policy-exception"
        reasons.append("The task needs model-backed work but the standing provider policy is disabled.")
    else:
        stage = TaskStageV1.EXPLORING if interpretation.research_requested else TaskStageV1.UNDERSTANDING
        state = TaskStateV1.EXPLORING if interpretation.research_requested else TaskStateV1.UNDERSTANDING
        strategy = "bounded-autonomous-execution"
        reasons.append("The task fits the standing project authority and resource policy.")
    route_skills = capability_route.get("skills")
    if not isinstance(route_skills, list):
        reasons.append("No capability route was available; execution must remain in understanding state.")
        if state not in {TaskStateV1.BLOCKED, TaskStateV1.NEEDS_APPROVAL}:
            state = TaskStateV1.BLOCKED
            stage = TaskStageV1.UNDERSTANDING
            strategy = "blocked-missing-capability-route"
    evidence = ["source-revision", "project-policy", "capability-route", "causal-task-events"]
    if interpretation.source_mutation_requested:
        evidence.extend(("isolated-worktree", "patch", "deterministic-validation", "independent-review"))
    if interpretation.research_requested:
        evidence.extend(("source-provenance", "contradictor-search", "uncertainty"))
    stopping = (
        "Stop on authority or path-boundary denial.",
        "Stop when required evidence is missing or contradictory.",
        "Stop when the task budget is exhausted.",
        "Stop at ready-for-review before integration.",
    )
    return SupervisorDecisionV1(
        "obtuse.blunder.supervisor-decision.v1",
        stage,
        state,
        strategy,
        selected_roles(interpretation),
        tuple(dict.fromkeys(evidence)),
        stopping,
        approval_kind,
        tuple(reasons),
    )


def decision_summary(decision: SupervisorDecisionV1, agent_ids: Sequence[str]) -> dict[str, object]:
    return {
        "schema": decision.schema,
        "stage": decision.stage.value,
        "state": decision.state.value,
        "strategy": decision.strategy,
        "selectedRoles": list(decision.selected_roles),
        "selectedAgents": list(agent_ids),
        "requiredEvidence": list(decision.required_evidence),
        "stoppingConditions": list(decision.stopping_conditions),
        "approvalKind": decision.approval_kind,
        "reasons": list(decision.reasons),
    }
