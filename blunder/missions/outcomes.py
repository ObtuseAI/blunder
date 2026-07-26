from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


OUTCOME_SCHEMA = "obtuse.blunder.mission-outcome.v1"
OUTCOME_STATUSES: frozenset[str] = frozenset({"integrated", "accepted", "reverted", "rejected"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def outcome_ledger(root: Path) -> Path:
    return root / "artifacts" / "obtuse" / "blunder-mission-learning-v1" / "outcomes.jsonl"


def read_outcomes(root: Path) -> list[dict[str, object]]:
    path = outcome_ledger(root)
    if not path.is_file():
        return []
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Mission outcome ledger is invalid at line {line_number}: {error}") from error
        if not isinstance(value, dict) or value.get("schema") != OUTCOME_SCHEMA:
            raise ValueError(f"Mission outcome ledger has an invalid record at line {line_number}.")
        records.append(value)
    return records


def record_outcome(
    root: Path,
    mission: Mapping[str, object],
    status: str,
    score: int | None,
    metrics: Mapping[str, object],
    note: str,
) -> dict[str, object]:
    normalized_status = status.strip().lower()
    if normalized_status not in OUTCOME_STATUSES:
        raise ValueError(f"Unsupported mission outcome status: {status}")
    if score is not None and (score < 1 or score > 5):
        raise ValueError("Mission outcome score must be from one through five.")
    capabilities = mission.get("capabilities") if isinstance(mission.get("capabilities"), dict) else {}
    skills = capabilities.get("skills") if isinstance(capabilities.get("skills"), list) else []
    worker = mission.get("worker") if isinstance(mission.get("worker"), dict) else {}
    report = worker.get("report") if isinstance(worker.get("report"), dict) else {}
    validation = report.get("validation") if isinstance(report.get("validation"), dict) else {}
    observed_metrics = {
        "changedFileCount": len(mission.get("changedFiles", [])),
        "validationPassed": validation.get("testsPassed") is True,
        "providerCalls": report.get("providerCalls"),
        **dict(metrics),
    }
    record = {
        "schema": OUTCOME_SCHEMA,
        "timestamp": utc_now(),
        "missionId": str(mission.get("id") or ""),
        "programId": mission.get("programId"),
        "status": normalized_status,
        "score": score,
        "metrics": observed_metrics,
        "note": note.strip()[:1_000],
        "skills": [str(item.get("id") or item.get("skill_id") or "") for item in skills if isinstance(item, dict)],
        "changedFiles": [str(item) for item in mission.get("changedFiles", [])],
        "budget": report.get("budget"),
        "source": "operator" if score is not None else "runtime",
        "permissionExpansion": False,
    }
    path = outcome_ledger(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")
    return record


def git_observation(root: Path, record: Mapping[str, object]) -> dict[str, object]:
    changed_files = [str(item) for item in record.get("changedFiles", [])]
    score = record.get("score")
    status = str(record.get("status") or "")
    if isinstance(score, int):
        quality = (score - 1) / 4
    elif status in {"reverted", "rejected"}:
        quality = 0.0
    elif status in {"integrated", "accepted"}:
        quality = 0.65
    else:
        quality = 0.5
    identity = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest().upper()
    if not (root / ".git").exists():
        return {
            "missionId": record.get("missionId"),
            "outcomeIdentity": identity,
            "status": status,
            "qualitySignal": round(quality, 3),
            "changedFilesPresent": None,
            "changedFileCount": len(changed_files),
            "missingFiles": [],
            "dirtyFiles": [],
            "repositoryObservation": "UNAVAILABLE_NOT_A_GIT_WORKTREE",
            "permissionExpansion": False,
        }
    status_process = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--", *changed_files],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    if status_process.returncode != 0:
        raise RuntimeError(f"Cannot observe mission outcome through Git: {status_process.stderr[-500:]}")
    missing = [path for path in changed_files if not (root / path).is_file()]
    dirty = [line[3:].replace("\\", "/") for line in status_process.stdout.splitlines() if len(line) > 3]
    if status in {"integrated", "accepted"} and not missing and not isinstance(score, int):
        quality = 0.8 if not dirty else 0.65
    return {
        "missionId": record.get("missionId"),
        "outcomeIdentity": identity,
        "status": status,
        "qualitySignal": round(quality, 3),
        "changedFilesPresent": len(changed_files) - len(missing),
        "changedFileCount": len(changed_files),
        "missingFiles": missing,
        "dirtyFiles": dirty,
        "repositoryObservation": "MEASURED",
        "permissionExpansion": False,
    }


def replay_evaluation(observations: Sequence[Mapping[str, object]]) -> dict[str, object]:
    labeled = [item for item in observations if isinstance(item.get("qualitySignal"), (int, float))]
    holdout = [item for item in labeled if int(hashlib.sha256(str(item.get("outcomeIdentity")).encode("utf-8")).hexdigest()[:8], 16) % 5 == 0]
    training = [item for item in labeled if item not in holdout]
    if len(training) < 3 or not holdout:
        return {"status": "INSUFFICIENT_HOLDOUT_EVIDENCE", "training": len(training), "holdout": len(holdout)}
    learned = sum(float(item["qualitySignal"]) for item in training) / len(training)
    learned_error = sum((float(item["qualitySignal"]) - learned) ** 2 for item in holdout) / len(holdout)
    baseline_error = sum((float(item["qualitySignal"]) - 0.5) ** 2 for item in holdout) / len(holdout)
    return {
        "status": "MEASURED",
        "training": len(training),
        "holdout": len(holdout),
        "learnedBrier": round(learned_error, 4),
        "baselineBrier": round(baseline_error, 4),
        "uplift": round(baseline_error - learned_error, 4),
    }


def outcome_summary(root: Path, selected_skills: Sequence[str]) -> dict[str, object]:
    records = read_outcomes(root)
    latest_by_mission: dict[str, dict[str, object]] = {}
    for record in records:
        latest_by_mission[str(record["missionId"])] = record
    latest = list(latest_by_mission.values())
    observations = [git_observation(root, record) for record in latest]
    observations_by_mission = {str(item.get("missionId") or ""): item for item in observations}
    scored = [record for record in latest if isinstance(record.get("score"), int)]
    accepted = [record for record in latest if record.get("status") in {"integrated", "accepted"}]
    reverted = [record for record in latest if record.get("status") == "reverted"]
    skill_signals: dict[str, dict[str, object]] = {}
    for skill_id in selected_skills:
        related = [record for record in latest if skill_id in record.get("skills", [])]
        related_scored = [record for record in related if isinstance(record.get("score"), int)]
        skill_signals[skill_id] = {
            "outcomes": len(related),
            "averageScore": round(sum(int(record["score"]) for record in related_scored) / len(related_scored), 3) if related_scored else None,
            "reverted": sum(1 for record in related if record.get("status") == "reverted"),
            "qualitySignal": round(
                sum(float(observations_by_mission[str(record["missionId"])]["qualitySignal"]) for record in related) / len(related),
                3,
            ) if related else None,
        }
    return {
        "schema": "obtuse.blunder.mission-learning-summary.v1",
        "outcomes": len(latest),
        "accepted": len(accepted),
        "reverted": len(reverted),
        "averageScore": round(sum(int(record["score"]) for record in scored) / len(scored), 3) if scored else None,
        "skillSignals": skill_signals,
        "automaticSignals": observations,
        "holdoutEvaluation": replay_evaluation(observations),
        "permissionExpansion": False,
        "routingPolicy": "penalize-reverted-prefer-high-score",
    }


def outcome_bias(summary: Mapping[str, object]) -> dict[str, float]:
    raw = summary.get("skillSignals")
    signals = raw if isinstance(raw, dict) else {}
    bias: dict[str, float] = {}
    for skill_id, value in signals.items():
        if not isinstance(value, dict) or int(value.get("outcomes") or 0) < 1:
            continue
        average = value.get("averageScore")
        score_signal = (float(average) - 3.0) * 1.5 if isinstance(average, (int, float)) else 0.0
        quality = value.get("qualitySignal")
        observed_signal = (float(quality) - 0.5) * 4.0 if isinstance(quality, (int, float)) else 0.0
        revert_penalty = min(9.0, int(value.get("reverted") or 0) * 3.0)
        bias[str(skill_id)] = round(score_signal + observed_signal - revert_penalty, 3)
    return bias
