from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence


ARENA_SCHEMA = "obtuse.blunder.agent-evaluation-arena.v1"
SUITE_SCHEMA = "obtuse.blunder.evaluation-suite.v1"
DIFF_PATH_PATTERN = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)
SHA256_PATTERN = re.compile(r"^[A-Fa-f0-9]{64}$")


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    category: str
    task: str
    expected_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    required_terms: tuple[str, ...]
    maximum_seconds: float
    maximum_tokens: int
    maximum_cost_usd: float


@dataclass(frozen=True)
class CandidateMeasurement:
    case_id: str
    candidate_id: str
    patch: str
    changed_paths: tuple[str, ...]
    validation_passed: bool
    reviewer_verdict: str
    court_verdict: str
    terminal_status: str
    operator_status: str | None
    elapsed_seconds: float
    total_tokens: int
    cost_usd: float


def require_string(value: Mapping[str, object], key: str, context: str) -> str:
    observed = str(value.get(key) or "").strip()
    if not observed:
        raise ValueError(f"{context} requires {key}.")
    return observed


def string_tuple(value: object, key: str, context: str, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValueError(f"{context} {key} must be a {'possibly empty' if allow_empty else 'non-empty'} list.")
    observed = tuple(str(item).replace("\\", "/").strip() for item in value)
    if any(not item for item in observed) or len(set(observed)) != len(observed):
        raise ValueError(f"{context} {key} contains an empty or duplicate value.")
    return observed


def parse_case(value: Mapping[str, object]) -> EvaluationCase:
    case_id = require_string(value, "id", "Evaluation case")
    context = f"Evaluation case {case_id}"
    maximum_seconds = float(value.get("maximumSeconds") or 0)
    maximum_tokens = int(value.get("maximumTokens") or 0)
    maximum_cost = float(value.get("maximumCostUsd") or 0)
    if maximum_seconds <= 0 or maximum_tokens <= 0 or maximum_cost <= 0:
        raise ValueError(f"{context} has an invalid budget.")
    return EvaluationCase(
        case_id=case_id,
        category=require_string(value, "category", context),
        task=require_string(value, "task", context),
        expected_paths=string_tuple(value.get("expectedPaths"), "expectedPaths", context, False),
        forbidden_paths=string_tuple(value.get("forbiddenPaths"), "forbiddenPaths", context, True),
        required_terms=string_tuple(value.get("requiredTerms"), "requiredTerms", context, False),
        maximum_seconds=maximum_seconds,
        maximum_tokens=maximum_tokens,
        maximum_cost_usd=maximum_cost,
    )


def load_suite(path: Path) -> tuple[EvaluationCase, ...]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read evaluation suite {path}: {error}") from error
    if not isinstance(value, dict) or value.get("schema") != SUITE_SCHEMA:
        raise ValueError(f"Evaluation suite has an unsupported contract: {path}")
    raw_cases = value.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases or any(not isinstance(item, dict) for item in raw_cases):
        raise ValueError("Evaluation suite cases must be a non-empty object list.")
    cases = tuple(parse_case(item) for item in raw_cases)
    identifiers = tuple(case.case_id for case in cases)
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Evaluation suite contains duplicate case ids.")
    return cases


def patch_paths(patch: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.group(2) for match in DIFF_PATH_PATTERN.finditer(patch)))


def parse_measurement(value: Mapping[str, object]) -> CandidateMeasurement:
    patch = str(value.get("patch") or "")
    raw_paths = value.get("changedPaths")
    changed = (
        tuple(str(item).replace("\\", "/") for item in raw_paths)
        if isinstance(raw_paths, list)
        else patch_paths(patch)
    )
    return CandidateMeasurement(
        case_id=require_string(value, "caseId", "Candidate measurement"),
        candidate_id=require_string(value, "candidateId", "Candidate measurement"),
        patch=patch,
        changed_paths=changed,
        validation_passed=value.get("validationPassed") is True,
        reviewer_verdict=str(value.get("reviewerVerdict") or "MISSING").upper(),
        court_verdict=str(value.get("courtVerdict") or "MISSING").upper(),
        terminal_status=str(value.get("terminalStatus") or "MISSING").lower(),
        operator_status=str(value["operatorStatus"]).lower() if value.get("operatorStatus") is not None else None,
        elapsed_seconds=float(value.get("elapsedSeconds") or 0),
        total_tokens=int(value.get("totalTokens") or 0),
        cost_usd=float(value.get("costUsd") or 0),
    )


def evaluate_case(case: EvaluationCase, measurement: CandidateMeasurement) -> dict[str, object]:
    changed = frozenset(measurement.changed_paths)
    expected = frozenset(case.expected_paths)
    forbidden = frozenset(case.forbidden_paths)
    patch_lower = measurement.patch.lower()
    required_observed = tuple(term for term in case.required_terms if term.lower() in patch_lower)
    path_recall = len(changed.intersection(expected)) / len(expected)
    path_precision = len(changed.intersection(expected)) / max(1, len(changed))
    path_safe = not changed.intersection(forbidden) and changed.issubset(expected)
    term_coverage = len(required_observed) / len(case.required_terms)
    budget_passed = (
        measurement.elapsed_seconds <= case.maximum_seconds
        and measurement.total_tokens <= case.maximum_tokens
        and measurement.cost_usd <= case.maximum_cost_usd
    )
    proof_passed = (
        measurement.validation_passed
        and measurement.reviewer_verdict == "APPROVE"
        and measurement.court_verdict == "APPROVE"
    )
    correctness = path_safe and path_recall == 1.0 and term_coverage == 1.0 and proof_passed and budget_passed
    operator_accepted = measurement.operator_status == "accepted"
    honest_terminal = measurement.terminal_status in {
        "completed",
        "failed",
        "interrupted",
        "cancelled",
        "inconclusive_timeout",
    }
    false_accept = operator_accepted and not correctness
    return {
        "schema": "obtuse.blunder.evaluation-case-result.v1",
        "caseId": case.case_id,
        "candidateId": measurement.candidate_id,
        "category": case.category,
        "correct": correctness,
        "pathRecall": round(path_recall, 4),
        "pathPrecision": round(path_precision, 4),
        "pathSafe": path_safe,
        "termCoverage": round(term_coverage, 4),
        "proofPassed": proof_passed,
        "budgetPassed": budget_passed,
        "honestTerminal": honest_terminal,
        "operatorIntervention": measurement.operator_status in {"rejected", "reverted"},
        "falseAccept": false_accept,
        "usage": {
            "elapsedSeconds": measurement.elapsed_seconds,
            "totalTokens": measurement.total_tokens,
            "costUsd": measurement.cost_usd,
        },
        "case": asdict(case),
    }


def validated_baseline(value: Mapping[str, object]) -> dict[str, object]:
    context = "External baseline"
    candidate_id = require_string(value, "candidateId", context)
    source = require_string(value, "source", context)
    measured_at = require_string(value, "measuredAt", context)
    measurement_sha256 = require_string(value, "measurementSha256", context)
    if SHA256_PATTERN.fullmatch(measurement_sha256) is None:
        raise ValueError(f"{context} {candidate_id} requires a 64-character measurementSha256.")
    raw_results = value.get("results")
    if not isinstance(raw_results, list) or not raw_results or any(not isinstance(item, dict) for item in raw_results):
        raise ValueError(f"{context} {candidate_id} results must be a non-empty object list.")
    return dict(value) | {
        "candidateId": candidate_id,
        "source": source,
        "measuredAt": measured_at,
        "measurementSha256": measurement_sha256.upper(),
        "results": [dict(item) for item in raw_results],
    }
def arena_report(
    cases: Sequence[EvaluationCase],
    measurements: Sequence[CandidateMeasurement],
    baselines: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    cases_by_id = {case.case_id: case for case in cases}
    results = [
        evaluate_case(cases_by_id[measurement.case_id], measurement)
        for measurement in measurements
        if measurement.case_id in cases_by_id
    ]
    measured = len(results)
    correct = sum(1 for result in results if result["correct"] is True)
    false_accepts = sum(1 for result in results if result["falseAccept"] is True)
    intervention = sum(1 for result in results if result["operatorIntervention"] is True)
    return {
        "schema": ARENA_SCHEMA,
        "status": "PASS" if measured and correct == measured and false_accepts == 0 else "NO_CANDIDATES" if not measured else "FAIL",
        "suiteCases": len(cases),
        "measuredCandidates": measured,
        "correctCandidates": correct,
        "correctnessRate": round(correct / measured, 4) if measured else None,
        "falseAcceptRate": round(false_accepts / measured, 4) if measured else None,
        "operatorInterventionRate": round(intervention / measured, 4) if measured else None,
        "averageCostUsd": round(
            sum(float(result["usage"]["costUsd"]) for result in results if isinstance(result.get("usage"), dict)) / measured,
            6,
        ) if measured else None,
        "results": results,
        "baselines": [validated_baseline(item) for item in baselines],
        "baselinePolicy": "external baselines are reported only when supplied with source and measurement provenance",
    }
