from __future__ import annotations

from typing import Mapping, Sequence


PROGRAM_CANARY_SCHEMA = "obtuse.blunder.program-canary-report.v1"


def claimed_paths(record: Mapping[str, object]) -> frozenset[str]:
    raw = record.get("claimedPaths")
    return frozenset(str(item).replace("\\", "/") for item in raw) if isinstance(raw, list) else frozenset()


def mission_budget_usage(record: Mapping[str, object]) -> Mapping[str, object]:
    worker = record.get("worker")
    report = worker.get("report") if isinstance(worker, dict) else None
    budget = report.get("budget") if isinstance(report, dict) else None
    return budget if isinstance(budget, dict) else {}


def budget_within_limits(record: Mapping[str, object]) -> bool:
    configured = record.get("budget")
    limits = configured if isinstance(configured, dict) else {}
    usage = mission_budget_usage(record)
    comparisons = (
        (float(usage.get("elapsedSeconds") or 0), float(limits.get("seconds") or 0)),
        (float(usage.get("providerCalls") or 0), float(limits.get("providerCalls") or 0)),
        (float(usage.get("totalTokens") or 0), float(limits.get("tokens") or 0)),
        (float(usage.get("costUsd") or 0), float(limits.get("costUsd") or 0)),
    )
    return all(limit > 0 and observed <= limit for observed, limit in comparisons)


def program_canary_report(program: Mapping[str, object]) -> dict[str, object]:
    raw_specialists = program.get("specialists")
    specialists = [item for item in raw_specialists if isinstance(item, dict)] if isinstance(raw_specialists, list) else []
    synthesis = program.get("synthesis")
    synthesis_record = synthesis if isinstance(synthesis, dict) else {}
    claims = [claimed_paths(record) for record in specialists]
    overlap: set[str] = set()
    observed: set[str] = set()
    for claim_set in claims:
        overlap.update(observed.intersection(claim_set))
        observed.update(claim_set)
    specialist_ready = all(
        record.get("status") == "completed" and record.get("integrationState") == "READY_TO_APPLY"
        for record in specialists
    )
    budgets_passed = bool(specialists) and all(budget_within_limits(record) for record in specialists)
    synthesis_worker = synthesis_record.get("worker")
    synthesis_report = synthesis_worker.get("report") if isinstance(synthesis_worker, dict) else None
    synthesis_result = synthesis_report.get("status") if isinstance(synthesis_report, dict) else None
    synthesis_passed = (
        synthesis_record.get("status") == "completed"
        and synthesis_record.get("integrationState") == "READY_TO_APPLY"
        and synthesis_result == "SYNTHESIZED_AND_VALIDATED"
    )
    gates = {
        "programCompleted": program.get("status") == "completed",
        "specialistCountBounded": 2 <= len(specialists) <= 3,
        "specialistClaimsNonempty": bool(claims) and all(claims),
        "specialistClaimsDisjoint": not overlap,
        "specialistsReadyToApply": specialist_ready,
        "specialistBudgetsPassed": budgets_passed,
        "synthesisValidated": synthesis_passed,
        "primaryTreeUntouched": all(record.get("integrationState") != "APPLIED_TO_REVIEW_TREE" for record in specialists),
    }
    return {
        "schema": PROGRAM_CANARY_SCHEMA,
        "programId": program.get("id"),
        "status": "PASS" if all(gates.values()) else "FAIL",
        "gates": gates,
        "specialistCount": len(specialists),
        "parallelWidth": len(specialists),
        "claimedPathCount": len(observed),
        "overlappingPaths": sorted(overlap),
        "specialistMissionIds": [str(record.get("id") or "") for record in specialists],
        "synthesisMissionId": synthesis_record.get("id"),
        "operatorIntegrationRequired": True,
    }


def campaign_summary(programs: Sequence[Mapping[str, object]]) -> dict[str, object]:
    reports = [program_canary_report(program) for program in programs]
    passed = sum(1 for report in reports if report["status"] == "PASS")
    return {
        "schema": "obtuse.blunder.program-canary-campaign.v1",
        "status": "PASS" if reports and passed == len(reports) else "NO_PROGRAMS" if not reports else "FAIL",
        "programs": len(reports),
        "passed": passed,
        "acceptanceRate": round(passed / len(reports), 4) if reports else None,
        "reports": reports,
    }
