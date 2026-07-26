from __future__ import annotations

from blunder.api.contracts import canonical_sha256
from blunder.inflow.models import BlunderInflowRecord, stable_id
from blunder.promotion_policy import (
    EVIDENCE_SCHEMA,
    PromotionEvidenceV1,
    PromotionScopeV1,
    evaluate_autonomous_promotion,
)


def evaluate_promotion(record: BlunderInflowRecord) -> list[dict[str, object]]:
    promotions: list[dict[str, object]] = []
    for candidate in record["capability_candidates"]:
        fixture_ids = [fixture["fixture_id"] for fixture in record["replay_fixtures"] if fixture["candidate_id"] == candidate["candidate_id"]]
        quarantine_preconditions_passed = (
            record["promotion_status"] == "quarantined"
            and record["risk_score"] == 0
            and bool(fixture_ids)
            and "REPLAY_FIXTURE_CREATED" in record["validation_refs"]
            and not record["contradiction_flags"]
        )
        evaluation_identity = canonical_sha256(
            {
                "recordId": record["record_id"],
                "candidateId": candidate["candidate_id"],
                "fixtureIds": fixture_ids,
                "validationRefs": record["validation_refs"],
                "contradictionFlags": record["contradiction_flags"],
            }
        )
        content_digest = str(record["content_hash"]).upper()
        if len(content_digest) != 64 or any(character not in "0123456789ABCDEF" for character in content_digest):
            content_digest = canonical_sha256({"contentHash": record["content_hash"]})
        decision = evaluate_autonomous_promotion(
            PromotionScopeV1.CAPABILITY,
            PromotionEvidenceV1(
                EVIDENCE_SCHEMA,
                str(candidate["candidate_id"]),
                evaluation_identity,
                (content_digest,),
                {
                    "constitutional": record["promotion_status"] == "quarantined",
                    "safety": record["risk_score"] == 0,
                    "provenance": bool(record["source_uri"]),
                    "rollback": True,
                    "authority": True,
                    "budget": True,
                    "reproducibility": bool(fixture_ids),
                },
                1.0 if quarantine_preconditions_passed else 0.0,
                min(record["trust_score"], record["replayability_score"]),
                1.0 - min(record["trust_score"], record["replayability_score"]),
                1,
                1,
                1,
                len(fixture_ids),
                1.0 if fixture_ids else 0.0,
                0.0,
                1.0,
                False,
                tuple(record["contradiction_flags"]),
                True,
                bool(record["source_uri"]),
                True,
                True,
                True,
                bool(fixture_ids),
            ),
        )
        promotions.append({
            "skill_token_id": stable_id([record["record_id"], str(candidate["candidate_id"]), "skill"]),
            "candidate_id": candidate["candidate_id"],
            "source_record_id": record["record_id"],
            "eligible": decision.promoted,
            "promotion_status": decision.status,
            "rollback_path_required": True,
            "demotion_path_required": True,
            "active_skill_token": decision.promoted_projection_activation_authorized,
            "reason": decision.status,
            "promotion_decision_digest": decision.decision_digest,
            "promotion_failed_gates": list(decision.failed_gates),
            "operator_promotion_required": False,
        })
    return promotions
