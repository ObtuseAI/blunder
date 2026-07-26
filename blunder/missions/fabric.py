from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from blunder.capabilities import CapabilityRegistry, route_task
from blunder.missions.branches import MissionBranchManager, atomic_write_json
from blunder.morphogenesis import OrganismBudget, classify_archetype, compile_organism, shadow_comparison
from blunder.repository_intelligence import build_repository_index, query_repository


PROGRAM_SCHEMA = "obtuse.blunder.mission-program.v1"
PROGRAM_ACTIVE: frozenset[str] = frozenset({"launching", "running", "ready_for_synthesis", "synthesizing"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def specialist_count(relevant_paths: list[str]) -> int:
    if len(relevant_paths) >= 6:
        return 3
    if len(relevant_paths) >= 2:
        return 2
    return 1


def partition_paths(paths: list[str], count: int) -> tuple[tuple[str, ...], ...]:
    buckets: list[list[str]] = [[] for _ in range(count)]
    for index, path in enumerate(paths):
        buckets[index % count].append(path)
    return tuple(tuple(bucket[:6]) for bucket in buckets if bucket)


def plan_program(root: Path, registry: CapabilityRegistry, task: str) -> dict[str, object]:
    normalized = task.strip()
    if len(normalized) < 3:
        raise ValueError("A mission program requires a concrete outcome.")
    route = route_task(registry, normalized, 5)
    if route.get("sourceMutationPossible") is not True:
        raise ValueError("This outcome routes to advisory work and does not require a coding program.")
    index = build_repository_index(root, True)
    ranked = query_repository(index, normalized, 18)
    candidates = [
        str(item.get("path") or "")
        for item in ranked
        if str(item.get("path") or "").startswith(("blunder/", "scripts/", "tests/", "docs/"))
        and float(item.get("score") or 0.0) >= 2.0
    ]
    if len(candidates) < 2:
        candidates = [
            str(item.get("path") or "")
            for item in ranked[:6]
            if str(item.get("path") or "").startswith(("blunder/", "scripts/", "tests/", "docs/"))
        ]
    relevant = list(dict.fromkeys(path for path in candidates if path))[:18]
    if not relevant:
        raise ValueError("Repository intelligence found no safe first-party paths for this outcome.")
    partitions = partition_paths(relevant, specialist_count(relevant))
    specialists = [
        {
            "id": f"specialist-{index + 1}",
            "role": "specialist",
            "dependsOn": [],
            "claimedPaths": list(claims),
            "task": (
                f"Deliver a cohesive specialist slice of this outcome: {normalized}. "
                f"You exclusively own these paths: {', '.join(claims)}. Do not modify any other path."
            ),
        }
        for index, claims in enumerate(partitions)
    ]
    synthesis = {
        "id": "synthesis",
        "role": "synthesis",
        "dependsOn": [item["id"] for item in specialists],
        "claimedPaths": relevant,
        "task": f"Combine reviewed specialist patches and validate the complete outcome: {normalized}",
    }
    organism_budget = OrganismBudget(1800, 3, 200000, 5.0)
    organism = compile_organism(
        classify_archetype(normalized),
        normalized,
        str(index.get("fingerprint") or ""),
        relevant,
        "high" if len(relevant) >= 12 else "medium",
        "high" if len(relevant) < 2 else "medium",
        ("repository-evidence", "validation-evidence", "dissent-evidence"),
        organism_budget,
        None,
        True,
    )
    static_budget = {
        "maximumSeconds": organism_budget.maximum_seconds,
        "maximumProviderCalls": organism_budget.maximum_provider_calls,
        "maximumTokens": organism_budget.maximum_tokens,
        "maximumCostUsd": organism_budget.maximum_cost_usd,
    }
    return {
        "schema": "obtuse.blunder.mission-program-plan.v1",
        "task": normalized,
        "nodes": specialists + [synthesis],
        "edgeCount": len(specialists),
        "parallelWidth": len(specialists),
        "repositoryIntelligence": {"fingerprint": index.get("fingerprint"), "counts": index.get("counts")},
        "capabilityRoute": route,
        "morphogenesisShadow": organism,
        "morphogenesisComparison": shadow_comparison(static_budget, organism, (), 12),
    }


class MissionFabricManager:
    def __init__(
        self,
        root: Path,
        registry: CapabilityRegistry,
        branches: MissionBranchManager,
    ) -> None:
        self._root = root.resolve()
        self._registry = registry
        self._branches = branches
        self._state_root = self._root / "artifacts" / "obtuse" / "blunder-mission-programs-v1"
        self._state_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, program_id: str) -> Path:
        if not program_id or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in program_id):
            raise ValueError("Mission program id is invalid.")
        return self._state_root / program_id / "program.json"

    def _read(self, program_id: str) -> dict[str, object]:
        path = self._path(program_id)
        if not path.is_file():
            raise ValueError("Mission program does not exist.")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema") != PROGRAM_SCHEMA:
            raise ValueError(f"Mission program record is invalid: {path}")
        return value

    def _write(self, program: Mapping[str, object]) -> None:
        atomic_write_json(self._path(str(program["id"])), program)

    def preview(self, task: str) -> dict[str, object]:
        return plan_program(self._branches.source_root(), self._registry, task)

    def create(self, task: str, provider_approved: bool) -> dict[str, object]:
        if not provider_approved:
            raise ValueError("A mission program requires explicit native model approval.")
        plan = self.preview(task)
        program_id = f"p-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        path = self._path(program_id)
        path.parent.mkdir(parents=True, exist_ok=False)
        program: dict[str, object] = {
            "schema": PROGRAM_SCHEMA,
            "id": program_id,
            "task": str(plan["task"]),
            "status": "launching",
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
            "plan": plan,
            "specialistMissionIds": [],
            "synthesisMissionId": None,
            "error": None,
        }
        self._write(program)
        started: list[str] = []
        try:
            nodes = plan.get("nodes")
            specialists = [item for item in nodes if isinstance(item, dict) and item.get("role") == "specialist"] if isinstance(nodes, list) else []
            for node in specialists:
                mission = self._branches.start_claimed(
                    str(node["task"]),
                    True,
                    [str(path) for path in node["claimedPaths"]],
                    program_id,
                    str(node["id"]),
                )
                started.append(str(mission["id"]))
            program["specialistMissionIds"] = started
            program["status"] = "running"
            program["updatedAt"] = utc_now()
            self._write(program)
            return program
        except Exception as error:
            for mission_id in started:
                self._branches.cancel(mission_id)
            program["status"] = "failed"
            program["updatedAt"] = utc_now()
            program["error"] = str(error)
            self._write(program)
            raise

    def get(self, program_id: str) -> dict[str, object]:
        with self._lock:
            program = self._read(program_id)
            mission_ids = [str(item) for item in program.get("specialistMissionIds", [])]
            specialists = [self._branches.get(mission_id) for mission_id in mission_ids]
            program["specialists"] = specialists
            statuses = {str(item.get("status")) for item in specialists}
            if program.get("synthesisMissionId"):
                synthesis = self._branches.get(str(program["synthesisMissionId"]))
                program["synthesis"] = synthesis
                program["status"] = "completed" if synthesis.get("status") == "completed" else str(synthesis.get("status"))
            elif specialists and all(item.get("integrationState") == "READY_TO_APPLY" for item in specialists):
                program["status"] = "ready_for_synthesis"
            elif statuses.intersection({"failed", "interrupted", "cancelled"}):
                program["status"] = "failed"
            elif statuses:
                program["status"] = "running"
            program["updatedAt"] = utc_now()
            self._write({key: value for key, value in program.items() if key not in {"specialists", "synthesis"}})
            return program

    def list(self) -> dict[str, object]:
        programs: list[dict[str, object]] = []
        for path in sorted(self._state_root.glob("*/program.json"), reverse=True):
            programs.append(self.get(path.parent.name))
        return {"schema": "obtuse.blunder.mission-program-list.v1", "programs": programs[:20]}

    def synthesize(self, program_id: str) -> dict[str, object]:
        with self._lock:
            program = self.get(program_id)
            if program.get("status") != "ready_for_synthesis":
                raise ValueError("Mission program is not ready for synthesis.")
            program["status"] = "synthesizing"
            self._write({key: value for key, value in program.items() if key != "specialists"})
            synthesis = self._branches.synthesize(
                program_id,
                str(program["task"]),
                [str(item) for item in program.get("specialistMissionIds", [])],
            )
            program["synthesisMissionId"] = synthesis["id"]
            program["status"] = "completed"
            program["updatedAt"] = utc_now()
            self._write({key: value for key, value in program.items() if key not in {"specialists", "synthesis"}})
            return self.get(program_id)
