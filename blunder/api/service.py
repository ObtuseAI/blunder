from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from blunder.agent_runtime import select_agent_contracts
from blunder.api.contracts import (
    ApprovalRequestV1,
    ArtifactRefV1,
    ProjectPolicyV1,
    TaskContractV1,
    TaskSnapshotV1,
    TaskStageV1,
    TaskStateV1,
    canonical_sha256,
    load_project_policy,
    public_value,
    require_exact_keys,
    require_identifier,
    require_text,
    parse_authority,
    parse_budget,
    strict_json_loads,
    utc_now,
)
from blunder.api.events import TaskEventLedger
from blunder.capabilities import CapabilityRegistry, load_registry, route_task
from blunder.environment_automation import detect_project_stacks
from blunder.objectives import build_contract_from_request, source_revision
from blunder.missions import MissionBranchManager
from blunder.promotion_policy import (
    PromotionLedger,
    PromotionScopeV1,
    evaluate_autonomous_promotion,
    parse_promotion_evidence,
)
from blunder.supervisor import decision_summary, supervise_task


TASK_SNAPSHOT_SCHEMA = "obtuse.blunder.task-snapshot.v1"


class VNextService:
    def __init__(self, repository_root: Path, policy_path: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.policy_path = policy_path.resolve()
        if not self.policy_path.is_relative_to(self.repository_root):
            raise ValueError("Blunder vNext project policy must be inside the repository.")
        self.policy = load_project_policy(self.repository_root, self.policy_path)
        self.capabilities: CapabilityRegistry = load_registry(self.repository_root)
        self.events = TaskEventLedger(self.repository_root)
        self.promotions = PromotionLedger(self.repository_root)
        self._mission_manager: MissionBranchManager | None = None
        self._root = self.repository_root / "artifacts" / "obtuse" / "blunder-vnext-v1"
        self._tasks_root = self._root / "tasks"
        self._lock = threading.RLock()

    def bind_mission_manager(self, manager: MissionBranchManager) -> None:
        if manager.source_root() != self.repository_root:
            raise ValueError("The vNext mission manager must own the configured repository root.")
        self._mission_manager = manager

    def _task_path(self, task_id: str) -> Path:
        return self._tasks_root / require_identifier(task_id, "Task id") / "snapshot.json"

    def _atomic_write(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        with temporary.open("xb") as handle:
            handle.write((json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)

    def _write_snapshot(self, snapshot: TaskSnapshotV1) -> None:
        self._atomic_write(self._task_path(snapshot.contract.task_id), public_value(snapshot))

    def _read_snapshot(self, task_id: str) -> TaskSnapshotV1:
        path = self._task_path(task_id)
        if not path.is_file():
            raise ValueError(f"Task does not exist: {task_id}")
        raw = strict_json_loads(path.read_text(encoding="utf-8"), f"Task snapshot {task_id}")
        if not isinstance(raw, dict) or raw.get("schema") != TASK_SNAPSHOT_SCHEMA:
            raise ValueError(f"Task snapshot is invalid: {path}")
        contract_raw = raw.get("contract")
        if not isinstance(contract_raw, dict):
            raise ValueError(f"Task snapshot contract is invalid: {path}")
        contract = self._contract_from_public(contract_raw)
        approvals_raw = raw.get("approvals")
        if not isinstance(approvals_raw, list):
            raise ValueError(f"Task snapshot approvals are invalid: {path}")
        approvals = tuple(self._approval_from_public(item) for item in approvals_raw)
        artifacts_raw = raw.get("artifacts")
        if not isinstance(artifacts_raw, list):
            raise ValueError(f"Task snapshot artifacts are invalid: {path}")
        artifacts = tuple(self._artifact_from_public(item) for item in artifacts_raw)
        try:
            stage = TaskStageV1(str(raw.get("stage")))
            state = TaskStateV1(str(raw.get("state")))
        except ValueError as error:
            raise ValueError(f"Task snapshot stage or state is invalid: {path}") from error
        selected_agents = self._public_text_tuple(raw.get("selectedAgents"), "selectedAgents")
        blockers = self._public_text_tuple(raw.get("blockers"), "blockers")
        event_digest = str(raw.get("eventDigest") or "")
        event_sequence = raw.get("eventSequence")
        if not isinstance(event_sequence, int) or isinstance(event_sequence, bool) or event_sequence < 1:
            raise ValueError(f"Task snapshot event sequence is invalid: {path}")
        if len(event_digest) != 64:
            raise ValueError(f"Task snapshot event digest is invalid: {path}")
        mission_id_raw = raw.get("missionId")
        mission_id = str(mission_id_raw) if mission_id_raw is not None else None
        return TaskSnapshotV1(
            TASK_SNAPSHOT_SCHEMA,
            contract,
            stage,
            state,
            require_text(raw.get("statusReason"), "Task statusReason", 4_000),
            selected_agents,
            artifacts,
            approvals,
            blockers,
            mission_id,
            event_sequence,
            event_digest,
            require_text(raw.get("updatedAt"), "Task updatedAt", 100),
        )

    def _contract_from_public(self, raw: Mapping[str, object]) -> TaskContractV1:
        from blunder.api.contracts import build_task_contract

        require_exact_keys(
            raw,
            frozenset(
                {
                    "schema",
                    "taskId",
                    "projectId",
                    "objective",
                    "constraints",
                    "successCriteria",
                    "deliverables",
                    "assumptions",
                    "unknowns",
                    "authority",
                    "budget",
                    "sourceRevision",
                    "createdAt",
                    "contractDigest",
                }
            ),
            "Task snapshot contract",
        )
        if raw.get("schema") != "obtuse.blunder.task-contract.v1":
            raise ValueError("Task snapshot contract schema is invalid.")

        authority_raw = raw.get("authority")
        budget_raw = raw.get("budget")
        if not isinstance(authority_raw, dict) or not isinstance(budget_raw, dict):
            raise ValueError("Task snapshot authority or budget is invalid.")
        authority = parse_authority(authority_raw)
        budget = parse_budget(budget_raw)
        contract = build_task_contract(
            str(raw.get("taskId") or ""),
            str(raw.get("projectId") or ""),
            str(raw.get("objective") or ""),
            self._public_text_tuple(raw.get("constraints"), "constraints"),
            self._public_text_tuple(raw.get("successCriteria"), "successCriteria"),
            self._public_text_tuple(raw.get("deliverables"), "deliverables"),
            self._public_text_tuple(raw.get("assumptions"), "assumptions"),
            self._public_text_tuple(raw.get("unknowns"), "unknowns"),
            authority,
            budget,
            str(raw.get("sourceRevision") or ""),
            str(raw.get("createdAt") or ""),
        )
        if contract.contract_digest != raw.get("contractDigest"):
            raise ValueError("Task snapshot contract digest does not match its contents.")
        return contract

    def _approval_from_public(self, value: object) -> ApprovalRequestV1:
        if not isinstance(value, dict):
            raise ValueError("Task approval must be an object.")
        require_exact_keys(
            value,
            frozenset(
                {
                    "approvalId",
                    "taskId",
                    "kind",
                    "reason",
                    "exactApprovePhrase",
                    "exactDenyPhrase",
                    "status",
                    "createdAt",
                    "decidedAt",
                }
            ),
            "Task approval",
        )
        decided_raw = value.get("decidedAt")
        return ApprovalRequestV1(
            require_identifier(value.get("approvalId"), "Approval id"),
            require_identifier(value.get("taskId"), "Approval task id"),
            require_text(value.get("kind"), "Approval kind", 128),
            require_text(value.get("reason"), "Approval reason", 4_000),
            require_text(value.get("exactApprovePhrase"), "Approval approve phrase", 256),
            require_text(value.get("exactDenyPhrase"), "Approval deny phrase", 256),
            require_text(value.get("status"), "Approval status", 32),
            require_text(value.get("createdAt"), "Approval createdAt", 100),
            require_text(decided_raw, "Approval decidedAt", 100) if decided_raw is not None else None,
        )

    def _artifact_from_public(self, value: object) -> ArtifactRefV1:
        if not isinstance(value, dict):
            raise ValueError("Task artifact must be an object.")
        require_exact_keys(
            value,
            frozenset({"artifactId", "taskId", "kind", "relativePath", "sha256", "bytes", "createdAt"}),
            "Task artifact",
        )
        relative_path = require_text(value.get("relativePath"), "Task artifact relativePath", 1_024)
        resolved = (self.repository_root / relative_path).resolve()
        if not resolved.is_relative_to(self.repository_root):
            raise ValueError(f"Task artifact path escapes the repository: {relative_path}")
        size = value.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("Task artifact bytes must be a non-negative integer.")
        digest = str(value.get("sha256") or "").upper()
        if len(digest) != 64 or any(character not in "0123456789ABCDEF" for character in digest):
            raise ValueError("Task artifact sha256 must be a 64-character digest.")
        return ArtifactRefV1(
            require_identifier(value.get("artifactId"), "Task artifact id"),
            require_identifier(value.get("taskId"), "Task artifact task id"),
            require_text(value.get("kind"), "Task artifact kind", 128),
            relative_path,
            digest,
            size,
            require_text(value.get("createdAt"), "Task artifact createdAt", 100),
        )

    def _public_text_tuple(self, value: object, context: str) -> tuple[str, ...]:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"Task snapshot {context} must be a text list.")
        return tuple(str(item) for item in value)

    def project_state(self) -> dict[str, object]:
        stack = detect_project_stacks(self.repository_root)
        return {
            "schema": "obtuse.blunder.project-list.v1",
            "projects": [
                {
                    "id": self.policy.project_id,
                    "name": self.repository_root.name,
                    "root": str(self.repository_root),
                    "sourceRevision": source_revision(self.repository_root),
                    "policy": public_value(self.policy),
                    "environment": public_value(stack),
                    "active": True,
                }
            ],
        }

    def open_project(self, body: Mapping[str, object]) -> dict[str, object]:
        if set(body) != {"root"}:
            raise ValueError("Open project request accepts only root.")
        requested = Path(require_text(body.get("root"), "Open project root", 1_024)).resolve()
        if str(requested) not in self.policy.allowed_roots or requested != self.repository_root:
            raise ValueError("Requested project root is outside the standing project policy.")
        return self.project_state()["projects"][0]  # type: ignore[index]

    def list_tasks(self) -> dict[str, object]:
        tasks = [
            public_value(self._synchronize_mission(self._read_snapshot(path.parent.name)))
            for path in sorted(self._tasks_root.glob("*/snapshot.json"), reverse=True)
        ]
        return {"schema": "obtuse.blunder.task-list.v1", "tasks": tasks[:100]}

    def get_task(self, task_id: str) -> dict[str, object]:
        with self._lock:
            return public_value(self._synchronize_mission(self._read_snapshot(task_id)))  # type: ignore[return-value]

    def _mission_artifact(self, snapshot: TaskSnapshotV1, record: Mapping[str, object]) -> ArtifactRefV1 | None:
        patch_text = str(record.get("patch") or "")
        if not patch_text:
            return None
        path = Path(patch_text).resolve()
        if not path.is_file() or not path.is_relative_to(self.repository_root):
            raise ValueError(f"Mission patch is missing or escapes the repository artifact boundary: {path}")
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest().upper()
        declared = str(record.get("patchSha256") or "").upper()
        if declared != digest:
            raise ValueError("Mission patch digest does not match its declared identity.")
        existing = next((item for item in snapshot.artifacts if item.sha256 == digest), None)
        if existing is not None:
            return existing
        return ArtifactRefV1(
            f"artifact-{uuid.uuid4().hex[:16]}",
            snapshot.contract.task_id,
            "mission-patch",
            path.relative_to(self.repository_root).as_posix(),
            digest,
            len(payload),
            utc_now(),
        )

    def _synchronize_mission(self, snapshot: TaskSnapshotV1) -> TaskSnapshotV1:
        if snapshot.mission_id is None or self._mission_manager is None:
            return snapshot
        record = self._mission_manager.get(snapshot.mission_id)
        status = str(record.get("status") or "unknown")
        artifact = self._mission_artifact(snapshot, record) if status == "completed" else None
        artifacts = snapshot.artifacts
        if artifact is not None and all(item.artifact_id != artifact.artifact_id and item.sha256 != artifact.sha256 for item in artifacts):
            artifacts = (*artifacts, artifact)
        if status in {"preparing", "running", "cancelling"}:
            stage = TaskStageV1.BUILDING
            state = TaskStateV1.BUILDING
            reason = f"Isolated mission {snapshot.mission_id} is {status}."
        elif status == "completed" and record.get("integrationState") == "READY_TO_APPLY":
            stage = TaskStageV1.READY_FOR_REVIEW
            state = TaskStateV1.READY_FOR_REVIEW
            reason = "The isolated mission completed with a verified patch ready for exact integration review."
        elif status == "completed":
            stage = TaskStageV1.VERIFYING
            state = TaskStateV1.BLOCKED
            reason = f"The isolated mission completed but its patch is not integrable: {record.get('integrationState')}."
        elif status == "cancelled":
            stage = snapshot.stage
            state = TaskStateV1.CANCELLED
            reason = "The isolated mission was cancelled and its process ownership was released."
        else:
            stage = TaskStageV1.VERIFYING
            state = TaskStateV1.BLOCKED
            reason = f"The isolated mission ended in {status}: {record.get('error') or 'review required'}."
        if snapshot.stage == stage and snapshot.state == state and snapshot.status_reason == reason and artifacts == snapshot.artifacts:
            return snapshot
        event = self.events.append(
            snapshot.contract.task_id,
            snapshot.contract.project_id,
            "mission.synchronized",
            stage,
            state,
            (snapshot.event_digest,),
            tuple(item.sha256 for item in artifacts if item not in snapshot.artifacts),
            (snapshot.mission_id,),
            {"providerCalls": 0, "costUsd": 0.0, "wallMilliseconds": 0},
            {
                "missionId": snapshot.mission_id,
                "missionStatus": status,
                "integrationState": record.get("integrationState"),
                "ownedProcess": True,
            },
            datetime.now(timezone.utc),
        )
        blockers = (reason,) if state == TaskStateV1.BLOCKED else ()
        synchronized = replace(
            snapshot,
            stage=stage,
            state=state,
            status_reason=reason,
            artifacts=artifacts,
            blockers=blockers,
            event_sequence=event.sequence,
            event_digest=event.event_digest,
            updated_at=event.created_at,
        )
        self._write_snapshot(synchronized)
        return synchronized

    def launch_task(self, task_id: str, body: Mapping[str, object]) -> dict[str, object]:
        require_exact_keys(body, frozenset(), "Task launch request")
        with self._lock:
            snapshot = self._synchronize_mission(self._read_snapshot(task_id))
            if self._mission_manager is None:
                raise ValueError("The isolated mission runtime is not bound to the vNext service.")
            if snapshot.mission_id is not None:
                raise ValueError("Task already has an isolated mission identity.")
            authority = snapshot.contract.authority
            provider = self.policy.provider
            if not authority.isolated_source_mutation:
                raise ValueError("Task does not require an isolated source mutation mission.")
            if not authority.provider or not authority.network or not provider.enabled:
                raise PermissionError("The standing project provider policy does not authorize this mission.")
            if provider.credential_reference is None or provider.maximum_calls_per_task < 1:
                raise PermissionError("The standing project provider policy lacks a credential reference or call budget.")
            if snapshot.contract.source_revision != source_revision(self.repository_root):
                raise ValueError("Task source identity is stale; create a new task against the current revision.")
            mission_budget = self._mission_manager.execution_budget()
            contract_budget = snapshot.contract.budget
            budget_gates = {
                "seconds": float(mission_budget["seconds"]) <= contract_budget.maximum_seconds,
                "providerCalls": int(mission_budget["providerCalls"]) <= contract_budget.maximum_provider_calls,
                "tokens": int(mission_budget["tokens"]) <= contract_budget.maximum_tokens,
                "costUsd": float(mission_budget["costUsd"]) <= contract_budget.maximum_cost_usd,
            }
            if not all(budget_gates.values()):
                raise PermissionError(f"Mission runtime budget exceeds the task contract: {budget_gates}")
            record = self._mission_manager.start_with_standing_policy(snapshot.contract.objective)
            mission_id = require_identifier(record.get("id"), "Mission id")
            event = self.events.append(
                task_id,
                snapshot.contract.project_id,
                "mission.launched",
                TaskStageV1.BUILDING,
                TaskStateV1.BUILDING,
                (snapshot.event_digest, snapshot.contract.contract_digest),
                (),
                (mission_id, self.policy.provider.credential_reference),
                {"providerCalls": 0, "costUsd": 0.0, "wallMilliseconds": 0},
                {"missionId": mission_id, "standingProviderPolicy": True, "budgetGates": budget_gates},
                datetime.now(timezone.utc),
            )
            launched = replace(
                snapshot,
                stage=TaskStageV1.BUILDING,
                state=TaskStateV1.BUILDING,
                status_reason="An isolated policy-bound coding mission is running.",
                mission_id=mission_id,
                blockers=(),
                event_sequence=event.sequence,
                event_digest=event.event_digest,
                updated_at=event.created_at,
            )
            self._write_snapshot(launched)
            return public_value(launched)  # type: ignore[return-value]

    def create_task(self, body: Mapping[str, object]) -> dict[str, object]:
        with self._lock:
            task_id = f"task-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
            contract, interpretation = build_contract_from_request(
                body,
                task_id,
                self.policy,
                source_revision(self.repository_root),
            )
            route = route_task(self.capabilities, contract.objective, 5)
            decision = supervise_task(contract, interpretation, self.policy, route)
            agents = select_agent_contracts(decision.selected_roles)
            approval: ApprovalRequestV1 | None = None
            if decision.approval_kind is not None:
                approval_id = f"approval-{uuid.uuid4().hex[:16]}"
                approval = ApprovalRequestV1(
                    approval_id,
                    task_id,
                    decision.approval_kind,
                    decision.reasons[0],
                    f"APPROVE {approval_id}",
                    f"DENY {approval_id}",
                    "PENDING",
                    utc_now(),
                    None,
                )
            event = self.events.append(
                task_id,
                contract.project_id,
                "task.created",
                decision.stage,
                decision.state,
                (contract.contract_digest,),
                (),
                ("project-policy", "capability-route"),
                {"providerCalls": 0, "costUsd": 0.0, "wallMilliseconds": 0},
                {
                    "interpretation": public_value(interpretation),
                    "supervisor": decision_summary(decision, tuple(agent.agent_id for agent in agents)),
                    "capabilityRouteDigest": canonical_sha256(route),
                },
                datetime.now(timezone.utc),
            )
            blockers = tuple(decision.reasons) if decision.state in {TaskStateV1.BLOCKED, TaskStateV1.NEEDS_APPROVAL} else ()
            snapshot = TaskSnapshotV1(
                TASK_SNAPSHOT_SCHEMA,
                contract,
                decision.stage,
                decision.state,
                decision.reasons[0],
                tuple(agent.agent_id for agent in agents),
                (),
                (approval,) if approval is not None else (),
                blockers,
                None,
                event.sequence,
                event.event_digest,
                event.created_at,
            )
            self._write_snapshot(snapshot)
            return public_value(snapshot)  # type: ignore[return-value]

    def cancel_task(self, task_id: str) -> dict[str, object]:
        with self._lock:
            snapshot = self._read_snapshot(task_id)
            if snapshot.state == TaskStateV1.CANCELLED:
                return public_value(snapshot)  # type: ignore[return-value]
            if snapshot.mission_id is not None:
                if self._mission_manager is None:
                    raise ValueError("Task has a mission identity but no bound mission runtime.")
                self._mission_manager.cancel(snapshot.mission_id)
            event = self.events.append(
                task_id,
                snapshot.contract.project_id,
                "task.cancelled",
                snapshot.stage,
                TaskStateV1.CANCELLED,
                (snapshot.event_digest,),
                (),
                (),
                {"providerCalls": 0, "costUsd": 0.0, "wallMilliseconds": 0},
                {"ownedProcessTerminationRequested": snapshot.mission_id is not None},
                datetime.now(timezone.utc),
            )
            cancelled = replace(
                snapshot,
                state=TaskStateV1.CANCELLED,
                status_reason="Task cancellation was requested by the authenticated operator.",
                blockers=(),
                event_sequence=event.sequence,
                event_digest=event.event_digest,
                updated_at=event.created_at,
            )
            self._write_snapshot(cancelled)
            return public_value(cancelled)  # type: ignore[return-value]

    def task_artifacts(self, task_id: str) -> dict[str, object]:
        snapshot = self._read_snapshot(task_id)
        return {
            "schema": "obtuse.blunder.task-artifact-list.v1",
            "taskId": task_id,
            "artifacts": [public_value(item) for item in snapshot.artifacts],
        }

    def decide_approval(self, approval_id: str, body: Mapping[str, object]) -> dict[str, object]:
        if set(body) != {"decision"}:
            raise ValueError("Approval decision request accepts only decision.")
        phrase = require_text(body.get("decision"), "Approval decision", 256)
        with self._lock:
            for path in sorted(self._tasks_root.glob("*/snapshot.json")):
                snapshot = self._read_snapshot(path.parent.name)
                for index, approval in enumerate(snapshot.approvals):
                    if approval.approval_id != approval_id:
                        continue
                    if approval.status != "PENDING":
                        raise ValueError("Approval has already been decided.")
                    if phrase == approval.exact_approve_phrase:
                        status = "APPROVED"
                        next_state = TaskStateV1.BLOCKED
                        reason = "Provider exception was recorded, but execution remains blocked until a credential reference and bounded policy are configured."
                    elif phrase == approval.exact_deny_phrase:
                        status = "DENIED"
                        next_state = TaskStateV1.BLOCKED
                        reason = "Provider authority was denied by the operator."
                    else:
                        raise ValueError("Approval decision must match the task-bound exact approve or deny phrase.")
                    decided = replace(approval, status=status, decided_at=utc_now())
                    approvals = list(snapshot.approvals)
                    approvals[index] = decided
                    event = self.events.append(
                        snapshot.contract.task_id,
                        snapshot.contract.project_id,
                        "approval.decided",
                        snapshot.stage,
                        next_state,
                        (snapshot.event_digest,),
                        (),
                        (approval_id,),
                        {"providerCalls": 0, "costUsd": 0.0, "wallMilliseconds": 0},
                        {"approvalId": approval_id, "status": status, "authorityGranted": False},
                        datetime.now(timezone.utc),
                    )
                    updated = replace(
                        snapshot,
                        state=next_state,
                        status_reason=reason,
                        approvals=tuple(approvals),
                        blockers=(reason,),
                        event_sequence=event.sequence,
                        event_digest=event.event_digest,
                        updated_at=event.created_at,
                    )
                    self._write_snapshot(updated)
                    return public_value(updated)  # type: ignore[return-value]
            raise ValueError(f"Approval does not exist: {approval_id}")

    def integrate_task(self, task_id: str, body: Mapping[str, object]) -> dict[str, object]:
        if set(body) != {"confirmation"}:
            raise ValueError("Task integration request accepts only confirmation.")
        snapshot = self._synchronize_mission(self._read_snapshot(task_id))
        confirmation = require_text(body.get("confirmation"), "Task integration confirmation", 256)
        if confirmation != f"INTEGRATE {task_id}":
            raise ValueError(f"Task integration requires exact confirmation: INTEGRATE {task_id}")
        if snapshot.state != TaskStateV1.READY_FOR_REVIEW or snapshot.mission_id is None:
            raise ValueError("Task is not backed by a verified isolated mission ready for review.")
        if self._mission_manager is None:
            raise ValueError("Task has a mission identity but no bound mission runtime.")
        record = self._mission_manager.integrate(snapshot.mission_id, f"INTEGRATE {snapshot.mission_id}")
        if record.get("integrationState") != "APPLIED_TO_REVIEW_TREE":
            raise ValueError("Mission integration did not reach the review-tree applied state.")
        event = self.events.append(
            task_id,
            snapshot.contract.project_id,
            "task.integrated",
            TaskStageV1.READY_FOR_REVIEW,
            TaskStateV1.READY_FOR_REVIEW,
            (snapshot.event_digest,),
            tuple(item.sha256 for item in snapshot.artifacts),
            (snapshot.mission_id,),
            {"providerCalls": 0, "costUsd": 0.0, "wallMilliseconds": 0},
            {"missionId": snapshot.mission_id, "integrationState": record["integrationState"], "commit": False, "push": False},
            datetime.now(timezone.utc),
        )
        integrated = replace(
            snapshot,
            status_reason="The verified mission patch was applied to the uncommitted review tree.",
            event_sequence=event.sequence,
            event_digest=event.event_digest,
            updated_at=event.created_at,
        )
        self._write_snapshot(integrated)
        return public_value(integrated)  # type: ignore[return-value]

    def event_page(self, cursor: int, task_id: str | None, limit: int) -> dict[str, object]:
        return self.events.public_events_after(cursor, task_id, limit)

    def recovery_state(self) -> dict[str, object]:
        return self.events.recovery_report()

    def promotion_state(self) -> dict[str, object]:
        return self.promotions.state()

    def evaluate_promotion(self, body: Mapping[str, object]) -> dict[str, object]:
        require_exact_keys(body, frozenset({"scope", "evidence"}), "Promotion evaluation request")
        scope_text = require_text(body.get("scope"), "Promotion scope", 64)
        try:
            scope = PromotionScopeV1(scope_text)
        except ValueError as error:
            allowed = [item.value for item in PromotionScopeV1]
            raise ValueError(f"Promotion scope is invalid; allowed={allowed}.") from error
        evidence = parse_promotion_evidence(body.get("evidence"))
        decision = evaluate_autonomous_promotion(scope, evidence)
        record = self.promotions.append(decision, datetime.now(timezone.utc))
        return {
            "schema": "obtuse.blunder.promotion-evaluation-result.v1",
            "decision": public_value(decision),
            "ledgerRecord": record,
        }
