from __future__ import annotations

import hashlib
import hmac
import unittest
from datetime import datetime, timedelta, timezone

from blunder.cognitive_ecology import (
    CAPSULE_SCHEMA,
    CapsuleChecks,
    GenomeEvaluation,
    bounded_schedule,
    build_genome_archive,
    build_memory_record,
    build_mutation_genome,
    capsule_signing_payload,
    federation_envelope,
    memory_transition,
    reputation_posterior,
    reproduce_genome,
    verify_and_quarantine_capsule,
)


SIGNING_KEY = b"epoch-five-fixture-signing-key"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def genome(suffix: str):
    return build_mutation_genome(
        prompts=(f"prompt-{suffix}",),
        context_rules=("bounded-context",),
        organ_topology=(("governor", "builder"), ("builder", "validator")),
        tools=("read-source", "run-validation"),
        non_root_policy=("no-activation", "no-credential-access"),
        evaluators=("historical-replay", "fresh-heldout"),
        compatibility=("ccos-v1",),
        lineage=(digest("parent"),),
        nondeterminism_bound=0.02,
    )


def verifier(payload: bytes, signature: str, signer: str) -> bool:
    expected = hmac.new(SIGNING_KEY, payload + signer.encode("utf-8"), hashlib.sha256).hexdigest().upper()
    return hmac.compare_digest(expected, signature)


def signed_capsule(genome_digest: str) -> dict[str, object]:
    capsule: dict[str, object] = {
        "schema": CAPSULE_SCHEMA,
        "genomeDigest": genome_digest,
        "provenance": {
            "predicateType": "https://slsa.dev/provenance/v1",
            "builder": "local-proof-builder",
            "materials": [digest("source")],
        },
        "compatibility": ["ccos-v1"],
        "evaluations": [digest("evaluation")],
        "authorityTransferred": False,
        "signatureAlgorithm": "HMAC-SHA256-TEST-VERIFIER",
        "signerIdentity": "fixture-signer",
    }
    payload = capsule_signing_payload(capsule)
    capsule["signature"] = hmac.new(
        SIGNING_KEY,
        payload + b"fixture-signer",
        hashlib.sha256,
    ).hexdigest().upper()
    return capsule


class BlunderCognitiveEcologyTests(unittest.TestCase):
    def test_genome_is_content_addressed_reproducible_and_archived_by_niche(self) -> None:
        first = genome("alpha")
        self.assertEqual(first, genome("alpha"))
        reproduction = reproduce_genome(first, first.genome_digest, 0.0)
        self.assertEqual(reproduction["status"], "REPRODUCED_WITHIN_BOUND")
        evaluation = GenomeEvaluation(
            genome_digest=first.genome_digest,
            model_identity="fixed-model",
            domain="defect-repair",
            fitness=(0.95, 1.0, 0.9),
            evidence_digest=digest("genome-evaluation"),
            reproducible=True,
        )
        archive = build_genome_archive(
            genomes=(first,),
            evaluations=(evaluation,),
            niches={"defect-repair:fixed-model": (first.genome_digest,)},
            supersessions={},
            tombstones=(),
        )
        self.assertTrue(archive["contentAddressed"])
        self.assertFalse(archive["automaticActivationAuthorized"])

    def test_memory_promotion_is_explainable_and_expiry_is_reversible(self) -> None:
        now = datetime(2026, 7, 14, tzinfo=timezone.utc)
        record = build_memory_record(
            tier="pattern",
            claim_digest=digest("claim"),
            evidence_digests=tuple(digest(f"evidence-{index}") for index in range(5)),
            independent_sources=4,
            domains=("repair", "architecture"),
            contradictor_searches=2,
            contradictions=(),
            confidence=0.92,
            uncertainty=0.1,
            created_at=now - timedelta(days=40),
            refreshed_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=30),
            supersedes=(),
            operator_approved=True,
        )
        transition = memory_transition(record, "principle", now)
        self.assertEqual(transition["status"], "PROMOTED_AUTONOMOUS_THRESHOLD_PASS")
        self.assertTrue(transition["automaticPromotionAuthorized"])
        self.assertFalse(transition["operatorPromotionRequired"])
        self.assertTrue(transition["reversible"])
        expired = memory_transition(record, "capability", now + timedelta(days=31))
        self.assertEqual(expired["direction"], "DEMOTE_OR_EXPIRE")

    def test_reputation_exposes_uncertainty_and_cannot_bypass_hard_gates(self) -> None:
        posterior = reputation_posterior("repair", 9, 1, 30.0, 30.0)
        self.assertGreater(posterior.uncertainty, 0.0)
        blocked = bounded_schedule(((digest("candidate"), posterior),), False, True, 1)
        self.assertEqual(blocked["status"], "HARD_GATE_BLOCKED")
        self.assertEqual(blocked["selected"], [])
        self.assertFalse(blocked["reputationCanBypassGates"])

    def test_unsigned_and_tampered_capsules_are_rejected(self) -> None:
        capsule = signed_capsule(genome("capsule").genome_digest)
        checks = CapsuleChecks(True, True, True, True, True)
        unsigned = dict(capsule)
        unsigned.pop("signature")
        self.assertEqual(verify_and_quarantine_capsule(unsigned, checks, verifier)["status"], "REJECTED")
        tampered = dict(capsule)
        tampered["genomeDigest"] = digest("tampered")
        self.assertEqual(verify_and_quarantine_capsule(tampered, checks, verifier)["status"], "REJECTED")

    def test_valid_capsule_still_arrives_quarantined_without_authority(self) -> None:
        capsule = signed_capsule(genome("valid").genome_digest)
        admitted = verify_and_quarantine_capsule(capsule, CapsuleChecks(True, True, True, True, True), verifier)
        self.assertEqual(admitted["status"], "QUARANTINED_ELIGIBLE_FOR_LOCAL_REVIEW")
        self.assertTrue(admitted["quarantined"])
        self.assertFalse(admitted["authorityTransferred"])
        self.assertFalse(admitted["automaticActivation"])

    def test_federation_transfers_claims_without_credentials_or_approval(self) -> None:
        with self.assertRaises(PermissionError):
            federation_envelope("peer", digest("card"), digest("task"), (digest("artifact"),), False)
        envelope = federation_envelope("peer", digest("card"), digest("task"), (digest("artifact"),), True)
        self.assertIsNone(envelope["credentials"])
        self.assertIsNone(envelope["privateMemory"])
        self.assertIsNone(envelope["approval"])
        self.assertFalse(envelope["authorityTransferred"])
        self.assertEqual(envelope["toolResourceBoundary"], "MCP")


if __name__ == "__main__":
    unittest.main()
