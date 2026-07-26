from __future__ import annotations

from blunder.inflow.license_gate import license_score
from blunder.inflow.models import SourceCandidate


def compute_trust_score(candidate: SourceCandidate, risk_flags: list[str], contradiction_count: int) -> float:
    secret_risk = 3.0 if any("KEY" in flag or "SECRET" in flag or "ENV" in flag for flag in risk_flags) else 0.0
    poison_risk = 2.0 if any("MALWARE" in flag or "EXPLOIT" in flag or "LEAKED" in flag for flag in risk_flags) else 0.0
    staleness_penalty = 0.4 if candidate["freshness_score"] < 0.5 else 0.0
    hallucination_risk = 0.7 if candidate["trust_class"] in {"AGENT_TRACE", "UNTRUSTED_QUARANTINE"} else 0.0
    contradiction_penalty = float(contradiction_count) * 0.35
    incompatibility_penalty = 2.0 if license_score(candidate["license_class"]) < 0 else 0.0
    unreplayable_penalty = 0.5 if candidate["replayability_score"] < 0.5 else 0.0
    score = (
        candidate["authority_score"]
        + license_score(candidate["license_class"])
        + candidate["relevance_score"]
        + candidate["freshness_score"]
        + candidate["reproducibility_score"]
        + candidate["validation_history_score"]
        + candidate["replayability_score"]
        + candidate["internal_alignment_score"]
        - secret_risk
        - poison_risk
        - staleness_penalty
        - hallucination_risk
        - contradiction_penalty
        - incompatibility_penalty
        - unreplayable_penalty
    )
    return round(score, 4)


def compute_risk_score(risk_flags: list[str], license_class: str) -> float:
    base = float(len(risk_flags)) * 0.5
    if license_score(license_class) < 0:
        base += 1.5
    return round(base, 4)
