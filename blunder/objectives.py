from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from blunder.api.contracts import (
    ProjectPolicyV1,
    TaskAuthorityV1,
    TaskBudgetV1,
    TaskContractV1,
    build_task_contract,
    require_exact_keys,
    require_object,
    require_text,
    require_text_tuple,
    utc_now,
)


MUTATION_TERMS = frozenset({"build", "change", "create", "edit", "fix", "implement", "refactor", "remove", "replace", "update"})
RESEARCH_TERMS = frozenset({"analyze", "compare", "discover", "investigate", "research", "review", "study"})
DESIGN_TERMS = frozenset({"architecture", "design", "interface", "prototype", "ui", "ux"})
EXTERNAL_TERMS = frozenset({"deploy", "external", "publish", "push", "release", "upload"})
TERM_PATTERN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class ObjectiveInterpretationV1:
    schema: str
    problem_types: tuple[str, ...]
    source_mutation_requested: bool
    research_requested: bool
    prototype_requested: bool
    external_effect_requested: bool
    clarification_required: bool
    clarification_reasons: tuple[str, ...]
    documented_assumptions: tuple[str, ...]


def normalized_terms(value: str) -> frozenset[str]:
    return frozenset(TERM_PATTERN.findall(value.lower()))


def interpret_objective(
    objective: str,
    constraints: tuple[str, ...],
    success_criteria: tuple[str, ...],
    deliverables: tuple[str, ...],
) -> ObjectiveInterpretationV1:
    terms = normalized_terms(" ".join((objective, *constraints, *deliverables)))
    effect_terms = normalized_terms(" ".join((objective, *deliverables)))
    source_mutation = bool(terms.intersection(MUTATION_TERMS))
    research = bool(terms.intersection(RESEARCH_TERMS))
    prototype = bool(terms.intersection(DESIGN_TERMS))
    external = bool(effect_terms.intersection(EXTERNAL_TERMS))
    problem_types: list[str] = []
    if research:
        problem_types.append("research")
    if prototype:
        problem_types.extend(("product", "design"))
    if source_mutation:
        problem_types.append("implementation")
    if not problem_types:
        problem_types.append("understanding")
    clarification: list[str] = []
    if source_mutation and not success_criteria:
        clarification.append("Source-changing work requires at least one success criterion.")
    if external:
        clarification.append("External effects require a separately authorized target and release boundary.")
    assumptions = (
        "All work remains confined to the configured project roots.",
        "Promotion is autonomous only through fixed constitutional thresholds; integration, commit, push, and deployment remain separately controlled.",
    )
    return ObjectiveInterpretationV1(
        "obtuse.blunder.objective-interpretation.v1",
        tuple(dict.fromkeys(problem_types)),
        source_mutation,
        research,
        prototype,
        external,
        bool(clarification),
        tuple(clarification),
        assumptions,
    )


def build_contract_from_request(
    body: Mapping[str, object],
    task_id: str,
    policy: ProjectPolicyV1,
    source_revision: str,
) -> tuple[TaskContractV1, ObjectiveInterpretationV1]:
    raw = require_object(body, "Task creation request")
    require_exact_keys(
        raw,
        frozenset({"objective", "constraints", "successCriteria", "deliverables", "assumptions", "unknowns"}),
        "Task creation request",
    )
    objective = require_text(raw["objective"], "Task objective", 4_000)
    constraints = require_text_tuple(raw["constraints"], "Task constraints", 64)
    success_criteria = require_text_tuple(raw["successCriteria"], "Task successCriteria", 64)
    deliverables = require_text_tuple(raw["deliverables"], "Task deliverables", 64)
    assumptions = require_text_tuple(raw["assumptions"], "Task assumptions", 64)
    unknowns = require_text_tuple(raw["unknowns"], "Task unknowns", 64)
    interpretation = interpret_objective(objective, constraints, success_criteria, deliverables)
    provider_allowed = policy.provider.enabled and not interpretation.external_effect_requested
    authority = TaskAuthorityV1(
        repository_read=True,
        research=interpretation.research_requested,
        prototype=interpretation.prototype_requested,
        isolated_source_mutation=interpretation.source_mutation_requested,
        provider=provider_allowed,
        network=provider_allowed,
        apply_to_review_tree=False,
        integrate=False,
        commit=False,
        push=False,
        deploy=False,
    )
    budget = TaskBudgetV1(
        maximum_seconds=1_800,
        maximum_provider_calls=policy.provider.maximum_calls_per_task if provider_allowed else 0,
        maximum_tokens=200_000 if provider_allowed else 0,
        maximum_cost_usd=policy.provider.maximum_cost_per_task_usd if provider_allowed else 0.0,
    )
    combined_assumptions = tuple(dict.fromkeys((*assumptions, *interpretation.documented_assumptions)))
    contract = build_task_contract(
        task_id,
        policy.project_id,
        objective,
        constraints,
        success_criteria,
        deliverables,
        combined_assumptions,
        unknowns,
        authority,
        budget,
        source_revision,
        utc_now(),
    )
    return contract, interpretation


def source_revision(repository_root: Path) -> str:
    root = repository_root.resolve()
    if not (root / ".git").exists():
        return "UNVERSIONED_LOCAL_SOURCE"
    completed = subprocess.run(
        ("git", "-C", str(root), "rev-parse", "HEAD"),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Git source revision probe failed for {root}: {completed.stderr.strip()}")
    revision = completed.stdout.strip().upper()
    if len(revision) != 40 or any(character not in "0123456789ABCDEF" for character in revision):
        raise ValueError(f"Git source revision is not a 40-character commit identity: {revision}")
    return revision
