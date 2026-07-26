from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from blunder.api.contracts import (
    canonical_sha256,
    public_value,
    require_boolean,
    require_digest,
    require_exact_keys,
    require_integer,
    require_number,
    require_object,
    require_text,
    strict_json_loads,
)


POLICY_SCHEMA = "obtuse.blunder.autonomous-promotion-policy.v1"
EVIDENCE_SCHEMA = "obtuse.blunder.promotion-evidence.v1"
DECISION_SCHEMA = "obtuse.blunder.promotion-decision.v1"
LEDGER_SCHEMA = "obtuse.blunder.promotion-ledger.v1"
LEDGER_RECORD_SCHEMA = "obtuse.blunder.promotion-ledger-record.v1"
MAX_PROMOTION_RECORDS = 100_000


class PromotionScopeV1(StrEnum):
    CAPABILITY = "capability"
    MEMORY = "memory"
    GENOME = "genome"
    ORGAN = "organ"
    AGENT_TEAM = "agent-team"
    RESEARCH_CONCEPT = "research-concept"
    STRATEGY = "strategy"
    IMPROVEMENT = "improvement"
    RELEASE_CANDIDATE = "release-candidate"


@dataclass(frozen=True)
class PromotionPolicyV1:
    schema: str
    policy_id: str
    scope: PromotionScopeV1
    minimum_evidence_completeness: float
    minimum_confidence: float
    maximum_uncertainty: float
    minimum_independent_sources: int
    minimum_independent_models: int
    minimum_domains: int
    minimum_holdout_trials: int
    minimum_holdout_success_rate: float
    minimum_improvement_ratio: float
    maximum_cost_ratio: float
    required_hard_gates: tuple[str, ...]
    promoted_projection_activation_allowed: bool
    policy_digest: str


@dataclass(frozen=True)
class PromotionEvidenceV1:
    schema: str
    candidate_identity: str
    evaluation_identity: str
    evidence_digests: tuple[str, ...]
    hard_gates: Mapping[str, bool]
    evidence_completeness: float
    confidence: float
    uncertainty: float
    independent_sources: int
    independent_models: int
    domains: int
    holdout_trials: int
    holdout_success_rate: float
    improvement_ratio: float
    cost_ratio: float
    contaminated: bool
    contradictions: tuple[str, ...]
    rollback_proven: bool
    provenance_complete: bool
    authority_within_envelope: bool
    budget_within_limit: bool
    source_unchanged: bool
    reproducible: bool


@dataclass(frozen=True)
class PromotionDecisionV1:
    schema: str
    scope: PromotionScopeV1
    candidate_identity: str
    policy_id: str
    policy_digest: str
    evaluation_identity: str
    evidence_digests: tuple[str, ...]
    gates: Mapping[str, bool]
    failed_gates: tuple[str, ...]
    status: str
    promoted: bool
    automatic_promotion_authorized: bool
    promoted_projection_activation_authorized: bool
    source_mutation_authorized: bool
    authority_expansion_authorized: bool
    external_effect_authorized: bool
    decision_digest: str


_COMMON_HARD_GATES: tuple[str, ...] = (
    "constitutional",
    "safety",
    "provenance",
    "rollback",
    "authority",
    "budget",
    "reproducibility",
)


_POLICY_THRESHOLDS: Mapping[PromotionScopeV1, tuple[float, float, float, int, int, int, int, float, float, float, bool]] = {
    PromotionScopeV1.CAPABILITY: (1.0, 0.90, 0.10, 2, 1, 1, 2, 0.90, 0.00, 1.10, True),
    PromotionScopeV1.MEMORY: (1.0, 0.90, 0.15, 2, 1, 2, 2, 0.90, 0.00, 1.10, True),
    PromotionScopeV1.GENOME: (1.0, 0.90, 0.10, 2, 2, 2, 3, 0.90, 0.01, 1.10, True),
    PromotionScopeV1.ORGAN: (1.0, 0.90, 0.10, 2, 2, 2, 3, 0.90, 0.01, 1.10, True),
    PromotionScopeV1.AGENT_TEAM: (1.0, 0.90, 0.10, 2, 2, 2, 3, 0.90, 0.01, 1.00, True),
    PromotionScopeV1.RESEARCH_CONCEPT: (1.0, 0.85, 0.15, 3, 1, 2, 2, 0.85, 0.01, 1.10, True),
    PromotionScopeV1.STRATEGY: (1.0, 0.90, 0.10, 2, 2, 2, 3, 0.90, 0.01, 1.00, True),
    PromotionScopeV1.IMPROVEMENT: (1.0, 0.95, 0.05, 3, 2, 2, 3, 0.90, 0.01, 1.00, False),
    PromotionScopeV1.RELEASE_CANDIDATE: (1.0, 0.95, 0.05, 3, 2, 2, 3, 0.95, 0.01, 1.00, False),
}


def constitutional_promotion_policy(scope: PromotionScopeV1) -> PromotionPolicyV1:
    values = _POLICY_THRESHOLDS[scope]
    payload: dict[str, object] = {
        "schema": POLICY_SCHEMA,
        "policyId": f"blunder-{scope.value}-promotion-v1",
        "scope": scope.value,
        "minimumEvidenceCompleteness": values[0],
        "minimumConfidence": values[1],
        "maximumUncertainty": values[2],
        "minimumIndependentSources": values[3],
        "minimumIndependentModels": values[4],
        "minimumDomains": values[5],
        "minimumHoldoutTrials": values[6],
        "minimumHoldoutSuccessRate": values[7],
        "minimumImprovementRatio": values[8],
        "maximumCostRatio": values[9],
        "requiredHardGates": list(_COMMON_HARD_GATES),
        "promotedProjectionActivationAllowed": values[10],
    }
    return PromotionPolicyV1(
        POLICY_SCHEMA,
        str(payload["policyId"]),
        scope,
        values[0],
        values[1],
        values[2],
        values[3],
        values[4],
        values[5],
        values[6],
        values[7],
        values[8],
        values[9],
        _COMMON_HARD_GATES,
        values[10],
        canonical_sha256(payload),
    )


def _validate_evidence(policy: PromotionPolicyV1, evidence: PromotionEvidenceV1) -> None:
    if evidence.schema != EVIDENCE_SCHEMA:
        raise ValueError(f"Promotion evidence schema must be {EVIDENCE_SCHEMA}.")
    require_text(evidence.candidate_identity, "Promotion candidate identity", 256)
    require_digest(evidence.evaluation_identity, "Promotion evaluation identity")
    if not evidence.evidence_digests:
        raise ValueError("Promotion evidence requires at least one content digest.")
    for digest in evidence.evidence_digests:
        require_digest(digest, "Promotion evidence digest")
    if len(set(evidence.evidence_digests)) != len(evidence.evidence_digests):
        raise ValueError("Promotion evidence digests must be unique.")
    if set(evidence.hard_gates) != set(policy.required_hard_gates):
        raise ValueError(
            "Promotion evidence hard gates must exactly match the constitutional policy; "
            f"required={sorted(policy.required_hard_gates)}, supplied={sorted(evidence.hard_gates)}."
        )
    if any(not isinstance(value, bool) for value in evidence.hard_gates.values()):
        raise ValueError("Promotion hard-gate observations must be booleans.")
    for name, value in (
        ("evidence completeness", evidence.evidence_completeness),
        ("confidence", evidence.confidence),
        ("uncertainty", evidence.uncertainty),
        ("holdout success rate", evidence.holdout_success_rate),
        ("improvement ratio", evidence.improvement_ratio),
    ):
        require_number(value, f"Promotion {name}", 0.0, 1.0)
    require_number(evidence.cost_ratio, "Promotion cost ratio", 0.0, 1_000.0)
    for name, value in (
        ("independent sources", evidence.independent_sources),
        ("independent models", evidence.independent_models),
        ("domains", evidence.domains),
        ("holdout trials", evidence.holdout_trials),
    ):
        require_integer(value, f"Promotion {name}", 0, 1_000_000)


def evaluate_autonomous_promotion(
    scope: PromotionScopeV1,
    evidence: PromotionEvidenceV1,
) -> PromotionDecisionV1:
    policy = constitutional_promotion_policy(scope)
    _validate_evidence(policy, evidence)
    gates: dict[str, bool] = {
        **dict(evidence.hard_gates),
        "evidenceCompleteness": evidence.evidence_completeness >= policy.minimum_evidence_completeness,
        "confidence": evidence.confidence >= policy.minimum_confidence,
        "uncertainty": evidence.uncertainty <= policy.maximum_uncertainty,
        "independentSources": evidence.independent_sources >= policy.minimum_independent_sources,
        "independentModels": evidence.independent_models >= policy.minimum_independent_models,
        "crossDomain": evidence.domains >= policy.minimum_domains,
        "holdoutTrials": evidence.holdout_trials >= policy.minimum_holdout_trials,
        "holdoutSuccess": evidence.holdout_success_rate >= policy.minimum_holdout_success_rate,
        "improvement": evidence.improvement_ratio >= policy.minimum_improvement_ratio,
        "cost": evidence.cost_ratio <= policy.maximum_cost_ratio,
        "uncontaminated": not evidence.contaminated,
        "contradictionFree": not evidence.contradictions,
        "rollbackProven": evidence.rollback_proven,
        "provenanceComplete": evidence.provenance_complete,
        "authorityWithinEnvelope": evidence.authority_within_envelope,
        "budgetWithinLimit": evidence.budget_within_limit,
        "sourceUnchanged": evidence.source_unchanged,
        "reproducible": evidence.reproducible,
    }
    failed = tuple(sorted(name for name, passed in gates.items() if not passed))
    promoted = not failed
    status = (
        "PROMOTED_AUTONOMOUS_THRESHOLD_PASS"
        if promoted
        else "NO_CHAMPION"
        if scope in {PromotionScopeV1.GENOME, PromotionScopeV1.ORGAN, PromotionScopeV1.IMPROVEMENT}
        else "PROMOTION_BLOCKED_THRESHOLD_NOT_MET"
    )
    payload: dict[str, object] = {
        "schema": DECISION_SCHEMA,
        "scope": scope.value,
        "candidateIdentity": evidence.candidate_identity,
        "policyId": policy.policy_id,
        "policyDigest": policy.policy_digest,
        "evaluationIdentity": evidence.evaluation_identity,
        "evidenceDigests": list(evidence.evidence_digests),
        "gates": gates,
        "failedGates": list(failed),
        "status": status,
        "promoted": promoted,
        "automaticPromotionAuthorized": promoted,
        "promotedProjectionActivationAuthorized": promoted and policy.promoted_projection_activation_allowed,
        "sourceMutationAuthorized": False,
        "authorityExpansionAuthorized": False,
        "externalEffectAuthorized": False,
    }
    return PromotionDecisionV1(
        DECISION_SCHEMA,
        scope,
        evidence.candidate_identity,
        policy.policy_id,
        policy.policy_digest,
        evidence.evaluation_identity,
        evidence.evidence_digests,
        gates,
        failed,
        status,
        promoted,
        promoted,
        promoted and policy.promoted_projection_activation_allowed,
        False,
        False,
        False,
        canonical_sha256(payload),
    )


def parse_promotion_evidence(value: object) -> PromotionEvidenceV1:
    raw = require_object(value, "Promotion evidence")
    require_exact_keys(
        raw,
        frozenset(
            {
                "schema",
                "candidateIdentity",
                "evaluationIdentity",
                "evidenceDigests",
                "hardGates",
                "evidenceCompleteness",
                "confidence",
                "uncertainty",
                "independentSources",
                "independentModels",
                "domains",
                "holdoutTrials",
                "holdoutSuccessRate",
                "improvementRatio",
                "costRatio",
                "contaminated",
                "contradictions",
                "rollbackProven",
                "provenanceComplete",
                "authorityWithinEnvelope",
                "budgetWithinLimit",
                "sourceUnchanged",
                "reproducible",
            }
        ),
        "Promotion evidence",
    )
    digests_raw = raw["evidenceDigests"]
    contradictions_raw = raw["contradictions"]
    if not isinstance(digests_raw, list) or len(digests_raw) > 1_000:
        raise ValueError("Promotion evidenceDigests must be a bounded list.")
    if not isinstance(contradictions_raw, list) or len(contradictions_raw) > 1_000:
        raise ValueError("Promotion contradictions must be a bounded list.")
    hard_gates_raw = require_object(raw["hardGates"], "Promotion hardGates")
    return PromotionEvidenceV1(
        require_text(raw["schema"], "Promotion evidence schema", 128),
        require_text(raw["candidateIdentity"], "Promotion candidateIdentity", 256),
        require_digest(raw["evaluationIdentity"], "Promotion evaluationIdentity"),
        tuple(require_digest(item, "Promotion evidence digest") for item in digests_raw),
        {str(key): require_boolean(item, f"Promotion hard gate {key}") for key, item in hard_gates_raw.items()},
        require_number(raw["evidenceCompleteness"], "Promotion evidenceCompleteness", 0.0, 1.0),
        require_number(raw["confidence"], "Promotion confidence", 0.0, 1.0),
        require_number(raw["uncertainty"], "Promotion uncertainty", 0.0, 1.0),
        require_integer(raw["independentSources"], "Promotion independentSources", 0, 1_000_000),
        require_integer(raw["independentModels"], "Promotion independentModels", 0, 1_000_000),
        require_integer(raw["domains"], "Promotion domains", 0, 1_000_000),
        require_integer(raw["holdoutTrials"], "Promotion holdoutTrials", 0, 1_000_000),
        require_number(raw["holdoutSuccessRate"], "Promotion holdoutSuccessRate", 0.0, 1.0),
        require_number(raw["improvementRatio"], "Promotion improvementRatio", 0.0, 1.0),
        require_number(raw["costRatio"], "Promotion costRatio", 0.0, 1_000.0),
        require_boolean(raw["contaminated"], "Promotion contaminated"),
        tuple(require_text(item, "Promotion contradiction", 1_024) for item in contradictions_raw),
        require_boolean(raw["rollbackProven"], "Promotion rollbackProven"),
        require_boolean(raw["provenanceComplete"], "Promotion provenanceComplete"),
        require_boolean(raw["authorityWithinEnvelope"], "Promotion authorityWithinEnvelope"),
        require_boolean(raw["budgetWithinLimit"], "Promotion budgetWithinLimit"),
        require_boolean(raw["sourceUnchanged"], "Promotion sourceUnchanged"),
        require_boolean(raw["reproducible"], "Promotion reproducible"),
    )


class PromotionLedger:
    def __init__(self, repository_root: Path) -> None:
        self._root = repository_root.resolve() / "artifacts" / "obtuse" / "blunder-vnext-v1" / "promotions"
        self._records_root = self._root / "records"
        self._index_path = self._root / "index.json"
        self._lock = threading.RLock()

    def _empty_index(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": LEDGER_SCHEMA,
            "sequence": 0,
            "headDigest": None,
            "records": [],
        }
        return payload | {"indexDigest": canonical_sha256(payload)}

    def _atomic_write(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        with temporary.open("xb") as handle:
            handle.write((json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)

    def _load_index(self) -> dict[str, object]:
        if not self._index_path.is_file():
            return self._empty_index()
        raw = require_object(strict_json_loads(self._index_path.read_text(encoding="utf-8"), "Promotion ledger index"), "Promotion ledger index")
        if set(raw) != {"schema", "sequence", "headDigest", "records", "indexDigest"} or raw["schema"] != LEDGER_SCHEMA:
            raise ValueError("Promotion ledger index fields or schema are invalid.")
        digest = require_digest(raw["indexDigest"], "Promotion ledger index digest")
        payload = {key: item for key, item in raw.items() if key != "indexDigest"}
        if canonical_sha256(payload) != digest:
            raise ValueError("Promotion ledger index digest does not match its contents.")
        sequence = require_integer(raw["sequence"], "Promotion ledger sequence", 0, MAX_PROMOTION_RECORDS)
        records = raw["records"]
        if not isinstance(records, list) or len(records) != sequence:
            raise ValueError("Promotion ledger record count does not match its sequence.")
        previous: str | None = None
        for expected_sequence, item in enumerate(records, 1):
            record_ref = require_object(item, f"Promotion ledger record reference {expected_sequence}")
            if set(record_ref) != {"sequence", "digest", "decisionDigest", "path"}:
                raise ValueError("Promotion ledger record reference fields are invalid.")
            if require_integer(record_ref["sequence"], "Promotion ledger record sequence", 1, MAX_PROMOTION_RECORDS) != expected_sequence:
                raise ValueError("Promotion ledger sequences are not contiguous.")
            record_digest = require_digest(record_ref["digest"], "Promotion ledger record digest")
            relative = require_text(record_ref["path"], "Promotion ledger record path", 256)
            path = (self._root / relative).resolve()
            if not path.is_relative_to(self._records_root.resolve()) or not path.is_file():
                raise ValueError(f"Promotion ledger record is missing or escapes the ledger: {relative}")
            record = self._read_record(path, record_digest)
            if record["previousRecordDigest"] != previous:
                raise ValueError(f"Promotion ledger chain is broken at sequence {expected_sequence}.")
            previous = record_digest
        if raw["headDigest"] != previous:
            raise ValueError("Promotion ledger head digest does not match its final record.")
        return raw

    def _read_record(self, path: Path, expected_digest: str) -> dict[str, object]:
        raw = require_object(strict_json_loads(path.read_text(encoding="utf-8"), "Promotion ledger record"), "Promotion ledger record")
        if set(raw) != {"schema", "sequence", "createdAt", "decision", "previousRecordDigest", "recordDigest"}:
            raise ValueError(f"Promotion ledger record fields are invalid: {path}")
        if raw["schema"] != LEDGER_RECORD_SCHEMA:
            raise ValueError(f"Promotion ledger record schema is invalid: {path}")
        supplied = require_digest(raw["recordDigest"], "Promotion ledger record digest")
        if supplied != expected_digest or canonical_sha256({key: item for key, item in raw.items() if key != "recordDigest"}) != supplied:
            raise ValueError(f"Promotion ledger record digest does not match its contents: {path}")
        return raw

    def append(self, decision: PromotionDecisionV1, observed_at: datetime) -> dict[str, object]:
        if observed_at.tzinfo is None:
            raise ValueError("Promotion ledger timestamp must be timezone-aware.")
        with self._lock:
            index = self._load_index()
            decision_value = public_value(decision)
            for item in list(index["records"]):
                reference = require_object(item, "Promotion ledger record reference")
                if reference["decisionDigest"] == decision.decision_digest:
                    return self._read_record(self._root / str(reference["path"]), str(reference["digest"]))
            sequence = int(index["sequence"]) + 1
            if sequence > MAX_PROMOTION_RECORDS:
                raise ValueError("Promotion ledger capacity is exhausted; retention review is required.")
            payload: dict[str, object] = {
                "schema": LEDGER_RECORD_SCHEMA,
                "sequence": sequence,
                "createdAt": observed_at.astimezone(timezone.utc).isoformat(),
                "decision": decision_value,
                "previousRecordDigest": index["headDigest"],
            }
            record_digest = canonical_sha256(payload)
            record = payload | {"recordDigest": record_digest}
            relative = f"records/{sequence:08d}-{record_digest}.json"
            self._atomic_write(self._root / relative, record)
            references = list(index["records"])
            references.append(
                {
                    "sequence": sequence,
                    "digest": record_digest,
                    "decisionDigest": decision.decision_digest,
                    "path": relative,
                }
            )
            next_index: dict[str, object] = {
                "schema": LEDGER_SCHEMA,
                "sequence": sequence,
                "headDigest": record_digest,
                "records": references,
            }
            next_index["indexDigest"] = canonical_sha256(next_index)
            self._atomic_write(self._index_path, next_index)
            return record

    def records(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            index = self._load_index()
            return tuple(
                self._read_record(self._root / str(require_object(item, "Promotion ledger record reference")["path"]), str(require_object(item, "Promotion ledger record reference")["digest"]))
                for item in list(index["records"])
            )

    def state(self) -> dict[str, object]:
        with self._lock:
            index = self._load_index()
            records = self.records()
            promoted = sum(1 for record in records if require_object(record["decision"], "Promotion decision").get("promoted") is True)
            return {
                "schema": "obtuse.blunder.promotion-state.v1",
                "sequence": index["sequence"],
                "headDigest": index["headDigest"],
                "promotedCount": promoted,
                "blockedCount": len(records) - promoted,
                "records": list(records[-100:]),
                "policies": [public_value(constitutional_promotion_policy(scope)) for scope in PromotionScopeV1],
                "thresholdsAreConstitutional": True,
                "manualPromotionPathAvailable": False,
            }
