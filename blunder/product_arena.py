from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence


PRODUCT_ARENA_SCHEMA = "obtuse.blunder.product-evaluation-arena.v1"
PRODUCT_SUITE_SCHEMA = "obtuse.blunder.product-evaluation-suite.v1"
SHA256 = re.compile(r"^[A-Fa-f0-9]{64}$")
REPORT_WEIGHTS: Mapping[str, float] = {
    "blueprint": 10.0,
    "visual": 20.0,
    "device": 15.0,
    "platform": 15.0,
    "design": 10.0,
    "assets": 10.0,
    "polyglot": 10.0,
}
ACCEPTED_STATUSES: Mapping[str, frozenset[str]] = {
    "blueprint": frozenset({"READY_FOR_PRODUCT_PLANNING"}),
    "visual": frozenset({"PASS"}),
    "device": frozenset({"PASS"}),
    "platform": frozenset({"READY_FOR_EXECUTABLE_PROOF", "READY_FOR_HEADLESS_PROOF"}),
    "design": frozenset({"PASS"}),
    "assets": frozenset({"PASS"}),
    "polyglot": frozenset({"PASS"}),
}


@dataclass(frozen=True)
class ProductCase:
    case_id: str
    domain: str
    task: str
    required_reports: tuple[str, ...]
    minimum_score: float
    minimum_journey_completion: float
    maximum_performance_metric: float
    maximum_operator_interventions: int
    maximum_seconds: float
    maximum_cost_usd: float


@dataclass(frozen=True)
class ProductMeasurement:
    case_id: str
    candidate_id: str
    reports: Mapping[str, Mapping[str, object]]
    metrics: Mapping[str, object]
    operator_status: str | None
    elapsed_seconds: float
    cost_usd: float


def required_string(value: Mapping[str, object], key: str, context: str) -> str:
    observed = str(value.get(key) or "").strip()
    if not observed:
        raise ValueError(f"{context} requires {key}.")
    return observed


def parse_case(value: Mapping[str, object]) -> ProductCase:
    case_id = required_string(value, "id", "Product case")
    raw_reports = value.get("requiredReports")
    if not isinstance(raw_reports, list) or not raw_reports:
        raise ValueError(f"Product case {case_id} requires report names.")
    reports = tuple(str(item) for item in raw_reports)
    unknown = frozenset(reports).difference(REPORT_WEIGHTS)
    if unknown or len(set(reports)) != len(reports):
        raise ValueError(f"Product case {case_id} has unknown or duplicate reports: {', '.join(sorted(unknown))}")
    observed = ProductCase(
        case_id=case_id,
        domain=required_string(value, "domain", f"Product case {case_id}"),
        task=required_string(value, "task", f"Product case {case_id}"),
        required_reports=reports,
        minimum_score=float(value.get("minimumScore") or 0),
        minimum_journey_completion=float(value.get("minimumJourneyCompletion") or 0),
        maximum_performance_metric=float(value.get("maximumPerformanceMetric") or 0),
        maximum_operator_interventions=int(value.get("maximumOperatorInterventions") or 0),
        maximum_seconds=float(value.get("maximumSeconds") or 0),
        maximum_cost_usd=float(value.get("maximumCostUsd") or 0),
    )
    if not 1 <= observed.minimum_score <= 100 or not 0 < observed.minimum_journey_completion <= 1:
        raise ValueError(f"Product case {case_id} has invalid quality thresholds.")
    if observed.maximum_performance_metric <= 0 or observed.maximum_seconds <= 0 or observed.maximum_cost_usd <= 0:
        raise ValueError(f"Product case {case_id} has invalid performance or execution budgets.")
    if observed.maximum_operator_interventions < 0:
        raise ValueError(f"Product case {case_id} has invalid intervention budget.")
    return observed


def load_product_suite(path: Path) -> tuple[ProductCase, ...]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read product evaluation suite {path}: {error}") from error
    if not isinstance(value, dict) or value.get("schema") != PRODUCT_SUITE_SCHEMA:
        raise ValueError("Product evaluation suite uses an unsupported contract.")
    raw_cases = value.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases or any(not isinstance(item, dict) for item in raw_cases):
        raise ValueError("Product evaluation suite cases must be a non-empty object list.")
    cases = tuple(parse_case(item) for item in raw_cases)
    if len(set(item.case_id for item in cases)) != len(cases):
        raise ValueError("Product evaluation suite contains duplicate case ids.")
    return cases


def parse_measurement(value: Mapping[str, object]) -> ProductMeasurement:
    candidate_id = required_string(value, "candidateId", "Product measurement")
    raw_reports = value.get("reports")
    if not isinstance(raw_reports, dict) or any(not isinstance(item, dict) for item in raw_reports.values()):
        raise ValueError(f"Product measurement {candidate_id} reports must be an object of report objects.")
    raw_metrics = value.get("metrics")
    if not isinstance(raw_metrics, dict):
        raise ValueError(f"Product measurement {candidate_id} metrics must be an object.")
    return ProductMeasurement(
        case_id=required_string(value, "caseId", f"Product measurement {candidate_id}"),
        candidate_id=candidate_id,
        reports={str(key): item for key, item in raw_reports.items() if isinstance(item, dict)},
        metrics=raw_metrics,
        operator_status=str(value.get("operatorStatus")).lower() if value.get("operatorStatus") is not None else None,
        elapsed_seconds=float(value.get("elapsedSeconds") or 0),
        cost_usd=float(value.get("costUsd") or 0),
    )


def report_gate(report_name: str, reports: Mapping[str, Mapping[str, object]]) -> bool:
    report = reports.get(report_name)
    return isinstance(report, Mapping) and str(report.get("status") or "") in ACCEPTED_STATUSES[report_name]


def evaluate_product_case(case: ProductCase, measurement: ProductMeasurement) -> dict[str, object]:
    report_gates = {name: report_gate(name, measurement.reports) for name in case.required_reports}
    report_weight = sum(REPORT_WEIGHTS[name] for name in case.required_reports)
    earned_report_weight = sum(REPORT_WEIGHTS[name] for name, passed in report_gates.items() if passed)
    journey_completion = float(measurement.metrics.get("journeyCompletionRate") or 0)
    accessibility_critical = int(measurement.metrics.get("accessibilityCritical") or 0)
    performance_metric = float(measurement.metrics.get("performanceMetric") or 0)
    interventions = int(measurement.metrics.get("operatorInterventions") or 0)
    experience_gates = {
        "journeysPassed": journey_completion >= case.minimum_journey_completion,
        "accessibilityPassed": accessibility_critical == 0,
        "performancePassed": 0 < performance_metric <= case.maximum_performance_metric,
        "interventionBudgetPassed": interventions <= case.maximum_operator_interventions,
        "timeBudgetPassed": 0 < measurement.elapsed_seconds <= case.maximum_seconds,
        "costBudgetPassed": 0 <= measurement.cost_usd <= case.maximum_cost_usd,
    }
    experience_score = sum(1 for passed in experience_gates.values() if passed) / len(experience_gates) * 30
    report_score = earned_report_weight / report_weight * 70 if report_weight else 0
    score = round(report_score + experience_score, 3)
    correct = all(report_gates.values()) and all(experience_gates.values()) and score >= case.minimum_score
    false_accept = measurement.operator_status == "accepted" and not correct
    return {
        "schema": "obtuse.blunder.product-evaluation-case-result.v1",
        "caseId": case.case_id,
        "candidateId": measurement.candidate_id,
        "domain": case.domain,
        "status": "PASS" if correct else "FAIL",
        "score": score,
        "minimumScore": case.minimum_score,
        "reportGates": report_gates,
        "experienceGates": experience_gates,
        "metrics": dict(measurement.metrics),
        "falseAccept": false_accept,
        "operatorIntervention": measurement.operator_status in {"rejected", "reverted"},
        "usage": {"elapsedSeconds": measurement.elapsed_seconds, "costUsd": measurement.cost_usd},
        "case": asdict(case),
    }


def validated_baseline(value: Mapping[str, object]) -> dict[str, object]:
    candidate_id = required_string(value, "candidateId", "Product baseline")
    source = required_string(value, "source", f"Product baseline {candidate_id}")
    measured_at = required_string(value, "measuredAt", f"Product baseline {candidate_id}")
    digest = required_string(value, "measurementSha256", f"Product baseline {candidate_id}")
    if SHA256.fullmatch(digest) is None:
        raise ValueError(f"Product baseline {candidate_id} requires a 64-character measurement digest.")
    raw_results = value.get("results")
    if not isinstance(raw_results, list) or not raw_results or any(not isinstance(item, dict) for item in raw_results):
        raise ValueError(f"Product baseline {candidate_id} requires result provenance.")
    return dict(value) | {"candidateId": candidate_id, "source": source, "measuredAt": measured_at, "measurementSha256": digest.upper()}


def product_arena_report(
    cases: Sequence[ProductCase],
    measurements: Sequence[ProductMeasurement],
    baselines: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    cases_by_id = {item.case_id: item for item in cases}
    unknown = sorted({item.case_id for item in measurements}.difference(cases_by_id))
    if unknown:
        raise ValueError(f"Product measurements reference unknown cases: {', '.join(unknown)}")
    results = [evaluate_product_case(cases_by_id[item.case_id], item) for item in measurements]
    passed = sum(1 for item in results if item["status"] == "PASS")
    false_accepts = sum(1 for item in results if item["falseAccept"] is True)
    return {
        "schema": PRODUCT_ARENA_SCHEMA,
        "status": "PASS" if results and passed == len(results) and not false_accepts else "NO_CANDIDATES" if not results else "FAIL",
        "suiteCases": len(cases),
        "measuredCandidates": len(results),
        "passedCandidates": passed,
        "passRate": round(passed / len(results), 4) if results else None,
        "falseAcceptRate": round(false_accepts / len(results), 4) if results else None,
        "averageScore": round(sum(float(item["score"]) for item in results) / len(results), 3) if results else None,
        "results": results,
        "baselines": [validated_baseline(item) for item in baselines],
        "baselinePolicy": "baseline results require an external source and content-addressed measurement provenance",
    }
