from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from blunder.api.contracts import canonical_sha256, require_digest
from blunder.promotion_policy import (
    EVIDENCE_SCHEMA,
    PromotionEvidenceV1,
    PromotionScopeV1,
    evaluate_autonomous_promotion,
)


@dataclass(frozen=True)
class AgentContractV1:
    schema: str
    agent_id: str
    role: str
    supported_tasks: tuple[str, ...]
    accepted_inputs: tuple[str, ...]
    output_artifacts: tuple[str, ...]
    required_evidence: tuple[str, ...]
    tools: tuple[str, ...]
    authority_scopes: tuple[str, ...]
    failure_conditions: tuple[str, ...]
    version: str


@dataclass(frozen=True)
class AgentBudgetV1:
    maximum_seconds: int
    maximum_provider_calls: int
    maximum_tokens: int
    maximum_cost_usd: float


@dataclass(frozen=True)
class AgentInstanceV1:
    agent_id: str
    role: str
    causal_identity: str
    context_identity: str
    model_identity: str
    provider_identity: str
    budget: AgentBudgetV1


@dataclass(frozen=True)
class AgentTeamV1:
    schema: str
    task_digest: str
    agents: tuple[AgentInstanceV1, ...]
    genuine_model_independence: bool
    genuine_provider_independence: bool
    independence_limitation: str | None
    team_digest: str


@dataclass(frozen=True)
class AgentMailboxMessageV1:
    schema: str
    message_id: str
    sender_agent_id: str
    recipient_agent_id: str
    sender_causal_identity: str
    recipient_context_identity: str
    kind: str
    payload_digest: str
    evidence_digests: tuple[str, ...]


def contract_for_role(role: str) -> AgentContractV1:
    common_failures = (
        "missing-causal-identity",
        "missing-required-evidence",
        "authority-denied",
        "budget-exhausted",
    )
    definitions: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
        "problem-framing": (("understanding",), ("task-contract",), ("problem-brief",), ("repository:read",)),
        "project-analysis": (("understanding", "implementation"), ("engineering-graph",), ("project-analysis",), ("repository:read",)),
        "research": (("research",), ("evidence-needs",), ("research-brief",), ("repository:read", "evidence:write")),
        "evidence-critic": (("research", "verification"), ("research-brief",), ("contradiction-report",), ("evidence:read", "evidence:write")),
        "product-strategy": (("product",), ("problem-brief",), ("product-strategy",), ("evidence:read",)),
        "ux-design": (("design",), ("product-strategy",), ("design-concepts",), ("prototype:isolated",)),
        "visual-review": (("design", "verification"), ("design-concepts",), ("visual-evaluation",), ("evidence:write",)),
        "builder": (("implementation",), ("admitted-plan",), ("isolated-patch",), ("worktree:bounded-source-mutation",)),
        "code-review": (("implementation", "verification"), ("isolated-patch",), ("review-findings",), ("worktree:read", "evidence:write")),
        "validator": (("verification",), ("isolated-patch",), ("validation-report",), ("validation:declared-command",)),
        "historian": (("all",), ("task-events",), ("outcome-record",), ("evidence:write",)),
    }
    if role not in definitions:
        raise ValueError(f"Agent role is not registered: {role}")
    tasks, inputs, outputs, scopes = definitions[role]
    tools = tuple(scope.split(":", 1)[0] for scope in scopes)
    return AgentContractV1(
        "obtuse.blunder.agent-contract.v1",
        f"agent-{role}",
        role,
        tasks,
        inputs,
        outputs,
        ("causal-task-event", "project-policy", "source-revision"),
        tools,
        scopes,
        common_failures,
        "1.0.0",
    )


def select_agent_contracts(roles: Sequence[str]) -> tuple[AgentContractV1, ...]:
    return tuple(contract_for_role(role) for role in dict.fromkeys(roles))


def build_agent_team(
    roles: Sequence[str],
    model_identities: Sequence[str],
    provider_identities: Sequence[str],
    task_digest: str,
    budget: AgentBudgetV1,
) -> AgentTeamV1:
    selected_roles = tuple(dict.fromkeys(role.strip() for role in roles))
    if not selected_roles or any(not role for role in selected_roles):
        raise ValueError("Agent team requires unique non-empty roles.")
    if len(model_identities) != len(selected_roles) or len(provider_identities) != len(selected_roles):
        raise ValueError("Agent team requires one explicit model and provider identity per role.")
    task_identity = require_digest(task_digest, "Agent team task digest")
    if (
        budget.maximum_seconds < 1
        or budget.maximum_provider_calls < 0
        or budget.maximum_tokens < 0
        or budget.maximum_cost_usd < 0.0
    ):
        raise ValueError("Agent team budget is invalid.")
    agents: list[AgentInstanceV1] = []
    for index, role in enumerate(selected_roles):
        contract = contract_for_role(role)
        model = model_identities[index].strip()
        provider = provider_identities[index].strip()
        if not model or not provider:
            raise ValueError("Agent model and provider identities must not be empty.")
        causal = canonical_sha256({"taskDigest": task_identity, "role": role, "index": index, "kind": "causal"})
        context = canonical_sha256({"taskDigest": task_identity, "role": role, "index": index, "kind": "context"})
        agents.append(AgentInstanceV1(contract.agent_id, role, causal, context, model, provider, budget))
    models = {agent.model_identity for agent in agents}
    providers = {agent.provider_identity for agent in agents}
    model_independence = len(models) >= 2
    provider_independence = len(providers) >= 2
    limitation = None
    if not model_independence or not provider_independence:
        limitation = "Genuine model/provider independence is unavailable; causal and context separation is recorded but not misreported as independent evaluation."
    payload: dict[str, object] = {
        "schema": "obtuse.blunder.agent-team.v1",
        "taskDigest": task_identity,
        "agents": [
            {
                "agentId": agent.agent_id,
                "role": agent.role,
                "causalIdentity": agent.causal_identity,
                "contextIdentity": agent.context_identity,
                "modelIdentity": agent.model_identity,
                "providerIdentity": agent.provider_identity,
                "budget": agent.budget.__dict__,
            }
            for agent in agents
        ],
        "genuineModelIndependence": model_independence,
        "genuineProviderIndependence": provider_independence,
        "independenceLimitation": limitation,
    }
    return AgentTeamV1(
        "obtuse.blunder.agent-team.v1",
        task_identity,
        tuple(agents),
        model_independence,
        provider_independence,
        limitation,
        canonical_sha256(payload),
    )


def build_mailbox_message(
    team: AgentTeamV1,
    sender_agent_id: str,
    recipient_agent_id: str,
    kind: str,
    payload: Mapping[str, object],
    evidence_digests: Sequence[str],
) -> AgentMailboxMessageV1:
    by_id = {agent.agent_id: agent for agent in team.agents}
    if sender_agent_id == recipient_agent_id or sender_agent_id not in by_id or recipient_agent_id not in by_id:
        raise ValueError("Agent mailbox messages require distinct registered sender and recipient identities.")
    normalized_kind = kind.strip()
    if not normalized_kind:
        raise ValueError("Agent mailbox message kind must not be empty.")
    evidence = tuple(require_digest(item, "Agent mailbox evidence") for item in evidence_digests)
    if not evidence:
        raise ValueError("Agent mailbox messages require evidence.")
    sender = by_id[sender_agent_id]
    recipient = by_id[recipient_agent_id]
    payload_digest = canonical_sha256(dict(payload))
    message_id = canonical_sha256(
        {
            "teamDigest": team.team_digest,
            "sender": sender.causal_identity,
            "recipient": recipient.context_identity,
            "kind": normalized_kind,
            "payloadDigest": payload_digest,
            "evidenceDigests": evidence,
        }
    )
    return AgentMailboxMessageV1(
        "obtuse.blunder.agent-mailbox-message.v1",
        message_id,
        sender_agent_id,
        recipient_agent_id,
        sender.causal_identity,
        recipient.context_identity,
        normalized_kind,
        payload_digest,
        evidence,
    )


def evaluate_team_shadow(
    team: AgentTeamV1,
    baseline_scores: Sequence[float],
    candidate_scores: Sequence[float],
    equal_budget: bool,
    uncontaminated: bool,
    rollback_proven: bool,
) -> dict[str, object]:
    if len(baseline_scores) < 3 or len(baseline_scores) != len(candidate_scores):
        raise ValueError("Agent-team shadow evaluation requires at least three paired held-out scores.")
    if any(score < 0.0 or score > 1.0 for score in (*baseline_scores, *candidate_scores)):
        raise ValueError("Agent-team shadow scores must be between zero and one.")
    baseline_mean = sum(baseline_scores) / len(baseline_scores)
    candidate_mean = sum(candidate_scores) / len(candidate_scores)
    improvement = max(0.0, (candidate_mean - baseline_mean) / baseline_mean) if baseline_mean > 0.0 else 0.0
    evaluation_identity = canonical_sha256(
        {
            "teamDigest": team.team_digest,
            "baselineScores": list(baseline_scores),
            "candidateScores": list(candidate_scores),
            "equalBudget": equal_budget,
            "uncontaminated": uncontaminated,
        }
    )
    evidence_digests = tuple(
        canonical_sha256({"index": index, "baseline": baseline, "candidate": candidate})
        for index, (baseline, candidate) in enumerate(zip(baseline_scores, candidate_scores))
    )
    decision = evaluate_autonomous_promotion(
        PromotionScopeV1.AGENT_TEAM,
        PromotionEvidenceV1(
            EVIDENCE_SCHEMA,
            team.team_digest,
            evaluation_identity,
            evidence_digests,
            {
                "constitutional": True,
                "safety": True,
                "provenance": uncontaminated,
                "rollback": rollback_proven,
                "authority": True,
                "budget": equal_budget,
                "reproducibility": team.genuine_model_independence,
            },
            1.0,
            min(candidate_scores),
            1.0 - min(candidate_scores),
            len(evidence_digests),
            len({agent.model_identity for agent in team.agents}),
            len({agent.role for agent in team.agents}),
            len(candidate_scores),
            sum(1 for baseline, candidate in zip(baseline_scores, candidate_scores) if candidate > baseline)
            / len(candidate_scores),
            improvement,
            1.0 if equal_budget else 1_000.0,
            not uncontaminated,
            (),
            rollback_proven,
            uncontaminated,
            True,
            equal_budget,
            True,
            team.genuine_model_independence,
        ),
    )
    return {
        "schema": "obtuse.blunder.agent-team-shadow-evaluation.v1",
        "teamDigest": team.team_digest,
        "baselineMean": baseline_mean,
        "candidateMean": candidate_mean,
        "promotionStatus": decision.status,
        "automaticPromotionAuthorized": decision.automatic_promotion_authorized,
        "promotionDecisionDigest": decision.decision_digest,
        "failedGates": list(decision.failed_gates),
    }
