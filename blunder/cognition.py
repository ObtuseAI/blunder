"""Evidence-grounded strategy tournament for native Blunder tasks."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Mapping, Sequence


REQUIRED_LENSES: frozenset[str] = frozenset({"precision", "systems", "adversarial", "synthesis"})
WORD_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{2,}")


@dataclass(frozen=True)
class EvidenceItem:
    path: str
    digest: str
    relevance: float
    matched_terms: tuple[str, ...]
    excerpt: str


@dataclass(frozen=True)
class ResearchFinding:
    claim: str
    evidence_paths: tuple[str, ...]
    uncertainty: str


@dataclass(frozen=True)
class PlanCandidate:
    identifier: str
    lens: str
    thesis: str
    target_files: tuple[str, ...]
    steps: tuple[str, ...]
    evidence_paths: tuple[str, ...]
    validation: tuple[str, ...]
    rollback: str
    risks: tuple[str, ...]
    risk_controls: tuple[str, ...]
    prediction: str
    novelty: str


@dataclass(frozen=True)
class Strategy:
    findings: tuple[ResearchFinding, ...]
    candidates: tuple[PlanCandidate, ...]
    discovery_gaps: tuple[str, ...]


@dataclass(frozen=True)
class LearningRecord:
    status: str
    target_files: tuple[str, ...]
    lens: str


@dataclass(frozen=True)
class PlanScore:
    identifier: str
    evidence: float
    validation: float
    reversibility: float
    simplicity: float
    risk_control: float
    useful_novelty: float
    learning_value: float
    history_adjustment: float
    total: float
    rationale: tuple[str, ...]


@dataclass(frozen=True)
class PlanDecision:
    selected: PlanCandidate
    scores: tuple[PlanScore, ...]


def normalized_terms(value: str) -> frozenset[str]:
    separated = value.replace("_", " ").replace("-", " ")
    observed = {match.group(0).lower() for match in WORD_PATTERN.finditer(separated)}
    singular = {term[:-1] for term in observed if len(term) > 4 and term.endswith("s") and not term.endswith("ss")}
    return frozenset(observed.union(singular))


def mutation_lens(patch_type: str) -> str:
    terms = normalized_terms(patch_type)
    if terms.intersection({"failure", "rollback", "safety", "holdout", "contradiction", "adversarial"}):
        return "adversarial"
    if terms.intersection({"queue", "inflow", "registry", "population", "replenishment", "architecture"}):
        return "systems"
    if terms.intersection({"novelty", "compound", "scenario", "diversity", "synthesis"}):
        return "synthesis"
    return "precision"


def build_evidence(task: str, sources: Mapping[str, str], excerpt_limit: int) -> tuple[EvidenceItem, ...]:
    if excerpt_limit < 80:
        raise ValueError("Evidence excerpts must allow at least 80 characters.")
    task_words = normalized_terms(task)
    evidence: list[EvidenceItem] = []
    for path, content in sorted(sources.items()):
        path_words = normalized_terms(path)
        content_words = normalized_terms(content)
        matches = tuple(sorted(task_words.intersection(path_words.union(content_words))))
        path_matches = len(task_words.intersection(path_words))
        denominator = max(1, len(task_words))
        relevance = min(1.0, (len(matches) + path_matches) / denominator)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        evidence.append(
            EvidenceItem(
                path=path,
                digest=digest,
                relevance=round(relevance, 4),
                matched_terms=matches,
                excerpt=content[:excerpt_limit],
            )
        )
    return tuple(sorted(evidence, key=lambda item: (-item.relevance, item.path)))


def require_string(value: Mapping[str, object], key: str, context: str) -> str:
    result = str(value.get(key) or "").strip()
    if not result:
        raise ValueError(f"{context} is missing {key}.")
    return result


def require_strings(value: Mapping[str, object], key: str, context: str) -> tuple[str, ...]:
    raw = value.get(key)
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{context} {key} must be a non-empty list.")
    if any(not isinstance(item, str) or not item.strip() for item in raw):
        raise ValueError(f"{context} {key} must contain only non-empty strings.")
    result = tuple(item.strip() for item in raw)
    if len(set(result)) != len(result):
        raise ValueError(f"{context} {key} contains duplicate values.")
    return result


def optional_strings(value: Mapping[str, object], key: str, context: str) -> tuple[str, ...]:
    raw = value.get(key)
    if not isinstance(raw, list):
        raise ValueError(f"{context} {key} must be a list.")
    if any(not isinstance(item, str) or not item.strip() for item in raw):
        raise ValueError(f"{context} {key} must contain only non-empty strings.")
    result = tuple(item.strip() for item in raw)
    if len(set(result)) != len(result):
        raise ValueError(f"{context} {key} contains duplicate values.")
    return result


def parse_finding(value: object, index: int) -> ResearchFinding:
    if not isinstance(value, dict):
        raise ValueError(f"Research finding {index} must be an object.")
    context = f"Research finding {index}"
    return ResearchFinding(
        claim=require_string(value, "claim", context),
        evidence_paths=require_strings(value, "evidencePaths", context),
        uncertainty=require_string(value, "uncertainty", context),
    )


def parse_candidate(value: object, index: int) -> PlanCandidate:
    if not isinstance(value, dict):
        raise ValueError(f"Plan candidate {index} must be an object.")
    context = f"Plan candidate {index}"
    return PlanCandidate(
        identifier=require_string(value, "id", context),
        lens=require_string(value, "lens", context).lower(),
        thesis=require_string(value, "thesis", context),
        target_files=require_strings(value, "targetFiles", context),
        steps=require_strings(value, "steps", context),
        evidence_paths=require_strings(value, "evidencePaths", context),
        validation=require_strings(value, "validation", context),
        rollback=require_string(value, "rollback", context),
        risks=require_strings(value, "risks", context),
        risk_controls=require_strings(value, "riskControls", context),
        prediction=require_string(value, "prediction", context),
        novelty=require_string(value, "novelty", context),
    )


def parse_strategy(value: Mapping[str, object]) -> Strategy:
    raw_findings = value.get("findings")
    raw_candidates = value.get("candidates")
    if not isinstance(raw_findings, list) or not raw_findings:
        raise ValueError("Strategy findings must be a non-empty list.")
    if not isinstance(raw_candidates, list) or len(raw_candidates) != len(REQUIRED_LENSES):
        raise ValueError(f"Strategy must contain exactly {len(REQUIRED_LENSES)} plan candidates.")
    gaps = optional_strings(value, "discoveryGaps", "Strategy")
    return Strategy(
        findings=tuple(parse_finding(item, index) for index, item in enumerate(raw_findings, start=1)),
        candidates=tuple(parse_candidate(item, index) for index, item in enumerate(raw_candidates, start=1)),
        discovery_gaps=gaps,
    )


def validate_strategy(strategy: Strategy, evidence: Sequence[EvidenceItem], max_target_files: int) -> None:
    known_paths = frozenset(item.path for item in evidence)
    identifiers = tuple(candidate.identifier for candidate in strategy.candidates)
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Strategy plan identifiers must be unique.")
    lenses = frozenset(candidate.lens for candidate in strategy.candidates)
    if lenses != REQUIRED_LENSES:
        raise ValueError(f"Strategy lenses must be exactly: {', '.join(sorted(REQUIRED_LENSES))}.")
    for finding in strategy.findings:
        unknown = set(finding.evidence_paths).difference(known_paths)
        if unknown:
            raise ValueError(f"Research finding cites unknown evidence: {', '.join(sorted(unknown))}")
    for candidate in strategy.candidates:
        if len(candidate.target_files) > max_target_files:
            raise ValueError(f"Plan {candidate.identifier} exceeds the {max_target_files}-file mutation limit.")
        unknown = set(candidate.evidence_paths).difference(known_paths)
        if unknown:
            raise ValueError(f"Plan {candidate.identifier} cites unknown evidence: {', '.join(sorted(unknown))}")
        if len(candidate.steps) < 2:
            raise ValueError(f"Plan {candidate.identifier} needs at least two concrete steps.")
        if len(candidate.steps) > 8 or len(candidate.validation) > 6:
            raise ValueError(f"Plan {candidate.identifier} has an unbounded execution or validation list.")
        if len(candidate.risks) > 6 or len(candidate.risk_controls) > 6:
            raise ValueError(f"Plan {candidate.identifier} has an unbounded risk contract.")


def candidate_terms(candidate: PlanCandidate) -> frozenset[str]:
    combined = " ".join(
        (candidate.thesis, candidate.novelty, *candidate.steps, *candidate.target_files, *candidate.risk_controls)
    )
    return normalized_terms(combined)


def jaccard_similarity(left: frozenset[str], right: frozenset[str]) -> float:
    union = left.union(right)
    if not union:
        return 1.0
    return len(left.intersection(right)) / len(union)


def diversity_score(candidate: PlanCandidate, peers: Sequence[PlanCandidate]) -> float:
    own_terms = candidate_terms(candidate)
    similarities = tuple(
        jaccard_similarity(own_terms, candidate_terms(peer))
        for peer in peers
        if peer.identifier != candidate.identifier
    )
    return 1.0 - max(similarities, default=1.0)


def history_adjustment(candidate: PlanCandidate, history: Sequence[LearningRecord]) -> float:
    relevant = tuple(
        record
        for record in history
        if record.status in {"APPLIED_AND_VALIDATED", "ROLLED_BACK"}
        and set(record.target_files).intersection(candidate.target_files)
    )
    if not relevant:
        return 0.0
    successes = sum(1 for record in relevant if record.status == "APPLIED_AND_VALIDATED")
    failures = len(relevant) - successes
    lens_successes = sum(
        1 for record in relevant if record.status == "APPLIED_AND_VALIDATED" and record.lens == candidate.lens
    )
    return max(-5.0, min(5.0, successes * 0.75 + lens_successes * 0.5 - failures * 1.5))


def score_candidate(
    candidate: PlanCandidate,
    peers: Sequence[PlanCandidate],
    evidence: Sequence[EvidenceItem],
    history: Sequence[LearningRecord],
) -> PlanScore:
    evidence_by_path = {item.path: item for item in evidence}
    cited = tuple(evidence_by_path[path] for path in candidate.evidence_paths)
    evidence_score = min(25.0, 11.0 + 5.0 * len(cited) + 4.0 * sum(item.relevance for item in cited))
    validation_score = min(20.0, 8.0 + 4.0 * len(candidate.validation))
    reversibility_score = 10.0 if len(candidate.rollback) >= 20 else 5.0
    simplicity_score = max(2.0, 12.0 - len(candidate.target_files) - 0.75 * len(candidate.steps))
    uncovered_risks = max(0, len(candidate.risks) - len(candidate.risk_controls))
    risk_score = min(10.0, 4.0 + 2.0 * len(candidate.risk_controls) - 2.0 * uncovered_risks)
    diversity = diversity_score(candidate, peers)
    novelty_score = min(15.0, 3.0 + 9.0 * diversity + min(3.0, len(candidate.novelty) / 40.0))
    learning_score = min(10.0, 5.0 + min(3.0, len(candidate.prediction) / 35.0) + len(candidate.validation))
    adjustment = history_adjustment(candidate, history)
    total = round(
        evidence_score
        + validation_score
        + reversibility_score
        + simplicity_score
        + risk_score
        + novelty_score
        + learning_score
        + adjustment,
        3,
    )
    rationale = (
        f"evidence={len(cited)} cited workspace sources",
        f"diversity={diversity:.3f} against competing plans",
        f"validation={len(candidate.validation)} explicit gates",
        f"history={adjustment:+.2f} from prior outcomes",
    )
    return PlanScore(
        identifier=candidate.identifier,
        evidence=round(evidence_score, 3),
        validation=round(validation_score, 3),
        reversibility=round(reversibility_score, 3),
        simplicity=round(simplicity_score, 3),
        risk_control=round(risk_score, 3),
        useful_novelty=round(novelty_score, 3),
        learning_value=round(learning_score, 3),
        history_adjustment=round(adjustment, 3),
        total=total,
        rationale=rationale,
    )


def select_plan(
    strategy: Strategy,
    evidence: Sequence[EvidenceItem],
    history: Sequence[LearningRecord],
    max_target_files: int,
) -> PlanDecision:
    validate_strategy(strategy, evidence, max_target_files)
    scores = tuple(score_candidate(candidate, strategy.candidates, evidence, history) for candidate in strategy.candidates)
    ranked = tuple(sorted(scores, key=lambda score: (-score.total, score.identifier)))
    selected_id = ranked[0].identifier
    selected = next(candidate for candidate in strategy.candidates if candidate.identifier == selected_id)
    return PlanDecision(selected=selected, scores=ranked)


def strategy_report(strategy: Strategy, decision: PlanDecision, evidence: Sequence[EvidenceItem]) -> dict[str, object]:
    return {
        "research": {
            "evidence": [asdict(item) | {"excerpt": "<bounded-context>"} for item in evidence],
            "findings": [asdict(item) for item in strategy.findings],
            "discoveryGaps": list(strategy.discovery_gaps),
        },
        "tournament": {
            "selected": asdict(decision.selected),
            "scores": [asdict(score) for score in decision.scores],
        },
    }
