from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from blunder.cognition import normalized_terms
from blunder.repository_intelligence import build_repository_index, query_repository


DISCOVERY_SCHEMA = "obtuse.blunder.discovery-brief.v1"
CLAIM_PATTERN = re.compile(r"(?i)\b(?:pass|fail|current|stale|ready|blocked|safe|unsafe)\b")


@dataclass(frozen=True)
class DiscoveryEvidence:
    path: str
    sha256: str
    relevance: float
    symbols: tuple[str, ...]
    imports: tuple[str, ...]
    excerpt: str


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    lens: str
    statement: str
    evidence_paths: tuple[str, ...]
    falsifiable_prediction: str
    experiment: tuple[str, ...]
    proof_cost: int
    novelty: float
    uncertainty: str
    score: float


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def bounded_excerpt(content: str, terms: frozenset[str], maximum: int) -> str:
    lines = content.splitlines()
    matched = next(
        (index for index, line in enumerate(lines) if any(term in line.lower() for term in terms)),
        0,
    )
    start = max(0, matched - 4)
    excerpt = "\n".join(lines[start : start + 12])
    return excerpt[:maximum]


def evidence_from_record(root: Path, record: Mapping[str, object], question: str) -> DiscoveryEvidence:
    relative = str(record.get("path") or "")
    path = root / relative
    content = path.read_text(encoding="utf-8", errors="replace")
    raw_symbols = record.get("symbols")
    raw_imports = record.get("imports")
    symbols = tuple(str(item) for item in raw_symbols) if isinstance(raw_symbols, list) else ()
    imports = tuple(str(item) for item in raw_imports) if isinstance(raw_imports, list) else ()
    return DiscoveryEvidence(
        path=relative,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest().upper(),
        relevance=float(record.get("score") or 0),
        symbols=symbols,
        imports=imports,
        excerpt=bounded_excerpt(content, normalized_terms(question), 1_200),
    )


def contradiction_flags(evidence: Sequence[DiscoveryEvidence]) -> tuple[dict[str, object], ...]:
    flags: list[dict[str, object]] = []
    for item in evidence:
        terms = frozenset(match.group(0).lower() for match in CLAIM_PATTERN.finditer(item.excerpt))
        pairs = (
            ("pass", "fail", "PASS_FAIL_MIXED_CLAIM"),
            ("current", "stale", "CURRENT_STALE_MIXED_CLAIM"),
            ("ready", "blocked", "READY_BLOCKED_MIXED_CLAIM"),
            ("safe", "unsafe", "SAFE_UNSAFE_MIXED_CLAIM"),
        )
        for positive, negative, label in pairs:
            if positive in terms and negative in terms:
                flags.append(
                    {
                        "flag": label,
                        "path": item.path,
                        "resolution": "Prefer executable and timestamped evidence over unqualified text assertions.",
                    }
                )
    return tuple(flags)


def hypothesis_score(evidence: Sequence[DiscoveryEvidence], proof_cost: int, novelty: float, uncertainty: str) -> float:
    relevance = sum(item.relevance for item in evidence[:2])
    uncertainty_penalty = 2.0 if uncertainty != "low" else 0.0
    return round(relevance + novelty * 4.0 + max(0, 6 - proof_cost) - uncertainty_penalty, 4)


def build_hypotheses(question: str, evidence: Sequence[DiscoveryEvidence], contradictions: Sequence[Mapping[str, object]]) -> tuple[Hypothesis, ...]:
    if not evidence:
        return ()
    primary = evidence[0]
    secondary = evidence[1] if len(evidence) > 1 else primary
    precision = Hypothesis(
        hypothesis_id="precision-001",
        lens="precision",
        statement=f"The smallest leverage point for '{question}' is concentrated in {primary.path}.",
        evidence_paths=(primary.path,),
        falsifiable_prediction="A focused change at the primary evidence path passes its mapped tests without unrelated file changes.",
        experiment=("Inspect the primary symbols and mapped tests.", "Apply one bounded change in an isolated mission.", "Measure path precision and focused validation."),
        proof_cost=2,
        novelty=0.35,
        uncertainty="low" if primary.relevance >= 5 else "medium",
        score=0.0,
    )
    systems = Hypothesis(
        hypothesis_id="systems-001",
        lens="systems",
        statement=f"The behavior spans {primary.path} and {secondary.path}; their import and test contracts should be evaluated together.",
        evidence_paths=tuple(dict.fromkeys((primary.path, secondary.path))),
        falsifiable_prediction="A two-surface validation detects at least one dependency or proves the primary path is isolated.",
        experiment=("Map imports and test neighbors for both paths.", "Run the narrow tests, then the complete native suite.", "Compare failure provenance."),
        proof_cost=4,
        novelty=0.55,
        uncertainty="medium",
        score=0.0,
    )
    adversarial = Hypothesis(
        hypothesis_id="adversarial-001",
        lens="adversarial",
        statement=(
            "Conflicting repository claims may produce a false-ready decision."
            if contradictions
            else "The highest-risk failure is an apparently valid patch that changes behavior outside its declared semantic intent."
        ),
        evidence_paths=tuple(dict.fromkeys(str(item.get("path") or primary.path) for item in contradictions)) or (primary.path,),
        falsifiable_prediction="The intent firewall or executable proof rejects a seeded collateral or contradictory change.",
        experiment=("Seed one controlled collateral change in a temporary fixture.", "Run deterministic intent and verification courts.", "Require an explicit rejection finding."),
        proof_cost=3,
        novelty=0.7,
        uncertainty="low",
        score=0.0,
    )
    synthesis = Hypothesis(
        hypothesis_id="synthesis-001",
        lens="synthesis",
        statement="Combining the narrow primary change with adversarial intent checks should improve correctness without expanding mutation scope.",
        evidence_paths=tuple(dict.fromkeys((primary.path, adversarial.evidence_paths[0]))),
        falsifiable_prediction="The combined workflow improves held-out correctness or intervention rate while preserving the same path budget.",
        experiment=("Run the precision candidate through the evaluation arena.", "Run the intent-guarded candidate on the same case.", "Compare correctness, cost, and intervention."),
        proof_cost=5,
        novelty=0.85,
        uncertainty="medium",
        score=0.0,
    )
    hypotheses = (precision, systems, adversarial, synthesis)
    return tuple(
        Hypothesis(
            hypothesis_id=item.hypothesis_id,
            lens=item.lens,
            statement=item.statement,
            evidence_paths=item.evidence_paths,
            falsifiable_prediction=item.falsifiable_prediction,
            experiment=item.experiment,
            proof_cost=item.proof_cost,
            novelty=item.novelty,
            uncertainty=item.uncertainty,
            score=hypothesis_score(
                tuple(source for source in evidence if source.path in item.evidence_paths),
                item.proof_cost,
                item.novelty,
                item.uncertainty,
            ),
        )
        for item in hypotheses
    )


def build_discovery_brief(root: Path, question: str, evidence_limit: int) -> dict[str, object]:
    normalized = question.strip()
    if len(normalized) < 8:
        raise ValueError("Discovery questions require at least eight characters.")
    index = build_repository_index(root.resolve(), True)
    ranked = query_repository(index, normalized, evidence_limit)
    evidence = tuple(
        evidence_from_record(root.resolve(), record, normalized)
        for record in ranked
        if str(record.get("path") or "") and float(record.get("score") or 0) > 0
    )
    contradictions = contradiction_flags(evidence)
    hypotheses = build_hypotheses(normalized, evidence, contradictions)
    ordered = tuple(sorted(hypotheses, key=lambda item: (-item.score, item.hypothesis_id)))
    return {
        "schema": DISCOVERY_SCHEMA,
        "createdAt": utc_now(),
        "question": normalized,
        "repositoryFingerprint": index.get("fingerprint"),
        "evidence": [asdict(item) for item in evidence],
        "evidenceCount": len(evidence),
        "contradictions": list(contradictions),
        "hypotheses": [asdict(item) for item in ordered],
        "hypothesisCount": len(ordered),
        "selectedHypothesis": ordered[0].hypothesis_id if ordered else None,
        "status": "READY_FOR_EXPERIMENT" if ordered else "BLOCKED_NO_REPOSITORY_EVIDENCE",
        "providerCalls": 0,
        "sourceMutation": False,
        "permissionExpansion": False,
    }


def write_discovery_brief(path: Path, brief: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(brief), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path
