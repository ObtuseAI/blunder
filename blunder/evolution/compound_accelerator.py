"""Native adaptive compound-evolution planning for Blunder.

The planner is deliberately advisory. It ranks bounded mutation families and
emits pressure, archive, champion, and cooldown state. Existing Blunder
snapshot, validation, rollback, and promotion gates retain all write authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil, sqrt
from typing import Iterable, Mapping, Sequence


MUTATION_FAMILIES: tuple[str, ...] = (
    "validation",
    "rollback",
    "inflow",
    "queue",
    "novelty",
    "observability",
    "general",
)

PROTECTED_FAMILIES: frozenset[str] = frozenset({"validation", "rollback"})
BRANCH_COOLDOWN_REJECTIONS = 3
MAX_PRESSURE_LEVEL = 3.0
PRESSURE_STEP = 0.25
BASE_CANDIDATE_BUDGET = 1
MAX_CANDIDATE_BUDGET = 4


@dataclass(frozen=True)
class MutationOutcome:
    sequence: int
    run_id: str
    patch_type: str
    family: str
    fitness: float
    applied: bool
    promoted: bool
    rejected: bool


@dataclass(frozen=True)
class FamilyStats:
    family: str
    attempts: int
    applied: int
    promotions: int
    rejections: int
    consecutive_rejections: int
    promotion_rate: float
    best_fitness: float
    archive_candidate: str | None
    cooled_down: bool
    protected: bool
    selection_score: float


def classify_patch_family(patch_type: str) -> str:
    normalized = patch_type.upper()
    rules: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("rollback", ("ROLLBACK", "RESTORE", "DEMOTION")),
        ("validation", ("VALIDATION", "THRESHOLD", "PROOF", "TEST", "GATE")),
        ("inflow", ("INFLOW", "CAPABILITY", "REPLAY", "FIXTURE")),
        ("queue", ("QUEUE", "REPLENISH", "SCHEDULER")),
        ("novelty", ("NOVELTY", "DIVERSITY", "MAP_ELITE", "LINEAGE", "CHAMPION")),
        ("observability", ("STATUS", "HEALTH", "REPORT", "SUMMARY", "METRIC", "COMMENT", "LEDGER")),
    )
    for family, markers in rules:
        if any(marker in normalized for marker in markers):
            return family
    return "general"


def index_promotions(rows: Sequence[Mapping[str, object]]) -> dict[str, Mapping[str, object]]:
    indexed: dict[str, Mapping[str, object]] = {}
    for row in rows:
        run_id = str(row.get("runId") or "")
        if run_id:
            indexed[run_id] = row
    return indexed


def outcome_fitness(applied: bool, accepted: bool, promoted: bool, rejected: bool) -> float:
    if promoted:
        return 1.0
    if rejected:
        return 0.0
    if applied:
        return 0.65
    if accepted:
        return 0.4
    return 0.1


def build_outcomes(
    patch_rows: Sequence[Mapping[str, object]],
    promotion_rows: Sequence[Mapping[str, object]],
) -> list[MutationOutcome]:
    promotions = index_promotions(promotion_rows)
    outcomes: list[MutationOutcome] = []
    for sequence, patch in enumerate(patch_rows):
        run_id = str(patch.get("runId") or f"sequence-{sequence}")
        patch_type = str(patch.get("patchType") or "UNKNOWN")
        promotion = promotions.get(run_id, {})
        applied = patch.get("patchApplied") is True
        accepted = patch.get("candidateAccepted") is True
        promoted = promotion.get("promoted") is True or str(promotion.get("result") or "") == "PROMOTED"
        result = str(promotion.get("result") or patch.get("result") or "").upper()
        rejected = not promoted and (
            "REJECT" in result
            or "ROLLBACK" in result
            or "SKIP" in result
            or not applied
        )
        outcomes.append(
            MutationOutcome(
                sequence=sequence,
                run_id=run_id,
                patch_type=patch_type,
                family=classify_patch_family(patch_type),
                fitness=outcome_fitness(applied, accepted, promoted, rejected),
                applied=applied,
                promoted=promoted,
                rejected=rejected,
            )
        )
    return outcomes


def consecutive_rejections(outcomes: Sequence[MutationOutcome], family: str) -> int:
    count = 0
    for outcome in reversed(outcomes):
        if outcome.family != family:
            continue
        if not outcome.rejected:
            break
        count += 1
    return count


def stagnation_cycles(promotion_rows: Sequence[Mapping[str, object]]) -> int:
    count = 0
    for row in reversed(promotion_rows):
        promoted = row.get("promoted") is True or str(row.get("result") or "") == "PROMOTED"
        if promoted:
            break
        count += 1
    return count


def pressure_level(stagnation: int) -> float:
    additional = max(0, stagnation - 1) * PRESSURE_STEP
    return round(min(MAX_PRESSURE_LEVEL, 1.0 + additional), 2)


def candidate_budget(pressure: float) -> int:
    return min(MAX_CANDIDATE_BUDGET, max(BASE_CANDIDATE_BUDGET, ceil(BASE_CANDIDATE_BUDGET * pressure)))


def elite_for_family(outcomes: Iterable[MutationOutcome], family: str) -> MutationOutcome | None:
    eligible = [outcome for outcome in outcomes if outcome.family == family]
    if not eligible:
        return None
    return max(eligible, key=lambda outcome: (outcome.fitness, outcome.sequence))


def family_selection_score(
    attempts: int,
    promotions: int,
    rejection_streak: int,
    elite: MutationOutcome | None,
    pressure: float,
    protected: bool,
    cooled_down: bool,
) -> float:
    if cooled_down:
        return -1.0
    promotion_rate = promotions / max(attempts, 1)
    outcome_weight = 0.3 + promotion_rate * 2.0
    exploration = 1.0 / sqrt(attempts + 1)
    diversity = 0.35 if elite is None else max(0.0, 1.0 - elite.fitness) * 0.25
    pressure_bonus = max(0.0, pressure - 1.0) * exploration
    protection_bonus = 0.15 if protected else 0.0
    rejection_penalty = min(0.6, rejection_streak * 0.15)
    return round(outcome_weight + exploration + diversity + pressure_bonus + protection_bonus - rejection_penalty, 6)


def summarize_family(outcomes: Sequence[MutationOutcome], family: str, pressure: float) -> FamilyStats:
    family_outcomes = [outcome for outcome in outcomes if outcome.family == family]
    attempts = len(family_outcomes)
    applied = sum(1 for outcome in family_outcomes if outcome.applied)
    promotions = sum(1 for outcome in family_outcomes if outcome.promoted)
    rejections = sum(1 for outcome in family_outcomes if outcome.rejected)
    rejection_streak = consecutive_rejections(outcomes, family)
    elite = elite_for_family(outcomes, family)
    protected = family in PROTECTED_FAMILIES
    cooled_down = rejection_streak >= BRANCH_COOLDOWN_REJECTIONS and not protected
    score = family_selection_score(
        attempts,
        promotions,
        rejection_streak,
        elite,
        pressure,
        protected,
        cooled_down,
    )
    return FamilyStats(
        family=family,
        attempts=attempts,
        applied=applied,
        promotions=promotions,
        rejections=rejections,
        consecutive_rejections=rejection_streak,
        promotion_rate=round(promotions / max(attempts, 1), 6),
        best_fitness=elite.fitness if elite is not None else 0.0,
        archive_candidate=elite.run_id if elite is not None else None,
        cooled_down=cooled_down,
        protected=protected,
        selection_score=score,
    )


def build_compound_plan(
    patch_rows: Sequence[Mapping[str, object]],
    promotion_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    outcomes = build_outcomes(patch_rows, promotion_rows)
    stagnation = stagnation_cycles(promotion_rows)
    pressure = pressure_level(stagnation)
    stats = [summarize_family(outcomes, family, pressure) for family in MUTATION_FAMILIES]
    ranked = sorted(stats, key=lambda item: (-item.selection_score, item.family))
    active = [item for item in ranked if not item.cooled_down]
    best = max(outcomes, key=lambda outcome: (outcome.fitness, outcome.sequence), default=None)
    archive = [
        {
            "family": item.family,
            "candidate": item.archive_candidate,
            "fitness": item.best_fitness,
        }
        for item in stats
        if item.archive_candidate is not None
    ]
    return {
        "classification": "OBTUSE_BLUNDER_NATIVE_COMPOUND_EVOLUTION_PLAN_V1",
        "status": "PASS",
        "nativeDesign": {
            "mechanisms": [
                "adaptive mutation pressure",
                "outcome-weighted algorithm selection",
                "quality-diversity elite archive",
                "best-ever champion floor",
                "weak-branch cooldown",
            ],
        },
        "history": {
            "outcomes": len(outcomes),
            "promotions": sum(1 for outcome in outcomes if outcome.promoted),
            "rejections": sum(1 for outcome in outcomes if outcome.rejected),
            "stagnationCycles": stagnation,
        },
        "pressure": {
            "level": pressure,
            "candidateBudgetNext": candidate_budget(pressure),
            "maximumCandidateBudget": MAX_CANDIDATE_BUDGET,
        },
        "selectedFamily": active[0].family if active else None,
        "rankedFamilies": [asdict(item) for item in ranked],
        "qualityDiversityArchive": archive,
        "championFloor": {
            "candidate": best.run_id if best is not None else None,
            "fitness": best.fitness if best is not None else 0.0,
            "mustNotRegress": True,
        },
        "cooledDownFamilies": [item.family for item in stats if item.cooled_down],
        "protectedFamilies": sorted(PROTECTED_FAMILIES),
        "safety": {
            "advisoryOnly": True,
            "sourceMutationAuthorized": False,
            "benchmarkLaunchAuthorized": False,
            "providerCallAuthorized": False,
            "thresholdReductionAuthorized": False,
            "existingSnapshotValidationRollbackGatesRequired": True,
        },
    }
