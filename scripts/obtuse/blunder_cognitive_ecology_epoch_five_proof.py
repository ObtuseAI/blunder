from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

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


PROOF_SCHEMA = "obtuse.blunder.cognitive-ecology-epoch-five-proof.v1"
FIXTURE_KEY = b"bounded-epoch-five-proof-fixture"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def write_json(path: Path, value: Mapping[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def verifier(payload: bytes, signature: str, signer: str) -> bool:
    expected = hmac.new(FIXTURE_KEY, payload + signer.encode("utf-8"), hashlib.sha256).hexdigest().upper()
    return hmac.compare_digest(expected, signature)


def signed_capsule(genome_digest: str) -> dict[str, object]:
    capsule: dict[str, object] = {
        "schema": CAPSULE_SCHEMA,
        "genomeDigest": genome_digest,
        "provenance": {
            "predicateType": "https://slsa.dev/provenance/v1",
            "builder": "blunder-epoch-five-local-proof",
            "materials": [digest("source-tree")],
        },
        "compatibility": ["ccos-v1"],
        "evaluations": [digest("cross-model-domain-evaluation")],
        "authorityTransferred": False,
        "signatureAlgorithm": "HMAC-SHA256-FIXTURE-VERIFIER",
        "signerIdentity": "epoch-five-fixture-signer",
    }
    capsule["signature"] = hmac.new(
        FIXTURE_KEY,
        capsule_signing_payload(capsule) + b"epoch-five-fixture-signer",
        hashlib.sha256,
    ).hexdigest().upper()
    return capsule


def build_proof(root: Path, output_root: Path) -> dict[str, object]:
    output_root.mkdir(parents=True, exist_ok=False)
    first = build_mutation_genome(
        prompts=("bounded-repair",),
        context_rules=("evidence-first",),
        organ_topology=(("governor", "builder"), ("builder", "validator")),
        tools=("read-source", "run-validation"),
        non_root_policy=("no-activation", "no-credentials"),
        evaluators=("historical", "heldout", "adversarial"),
        compatibility=("ccos-v1",),
        lineage=(digest("parent-genome"),),
        nondeterminism_bound=0.02,
    )
    second = build_mutation_genome(
        prompts=("bounded-repair-v2",),
        context_rules=("evidence-first",),
        organ_topology=(("governor", "builder"), ("builder", "validator")),
        tools=("read-source", "run-validation"),
        non_root_policy=("no-activation", "no-credentials"),
        evaluators=("historical", "heldout", "adversarial"),
        compatibility=("ccos-v1",),
        lineage=(first.genome_digest,),
        nondeterminism_bound=0.02,
    )
    reproduction = reproduce_genome(second, second.genome_digest, 0.0)
    evaluations = (
        GenomeEvaluation(first.genome_digest, "fixed-model-a", "repair", (0.9, 1.0, 0.85), digest("eval-a"), True),
        GenomeEvaluation(second.genome_digest, "fixed-model-b", "architecture", (0.94, 1.0, 0.9), digest("eval-b"), True),
    )
    archive = build_genome_archive(
        genomes=(first, second),
        evaluations=evaluations,
        niches={"repair:model-a": (first.genome_digest,), "architecture:model-b": (second.genome_digest,)},
        supersessions={first.genome_digest: second.genome_digest},
        tombstones=(first.genome_digest,),
    )
    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    memory = build_memory_record(
        tier="theory",
        claim_digest=digest("durable-claim"),
        evidence_digests=tuple(digest(f"memory-evidence-{index}") for index in range(8)),
        independent_sources=8,
        domains=("repair", "architecture", "research"),
        contradictor_searches=6,
        contradictions=(),
        confidence=0.95,
        uncertainty=0.08,
        created_at=now - timedelta(days=60),
        refreshed_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=30),
        supersedes=(digest("old-memory"),),
        operator_approved=True,
    )
    promotion = memory_transition(memory, "architecture", now)
    expiry = memory_transition(memory, "law", now + timedelta(days=31))
    posterior = reputation_posterior("architecture", 18, 2, 45.0, 30.0)
    blocked_schedule = bounded_schedule(((second.genome_digest, posterior),), False, True, 1)
    capsule = signed_capsule(second.genome_digest)
    checks = CapsuleChecks(True, True, True, True, True)
    valid_admission = verify_and_quarantine_capsule(capsule, checks, verifier)
    unsigned = dict(capsule)
    unsigned.pop("signature")
    unsigned_admission = verify_and_quarantine_capsule(unsigned, checks, verifier)
    tampered = dict(capsule)
    tampered["genomeDigest"] = digest("tampered")
    tampered_admission = verify_and_quarantine_capsule(tampered, checks, verifier)
    federation = federation_envelope(
        "peer-fixture",
        digest("capability-card"),
        digest("task"),
        (digest("artifact"),),
        True,
    )
    errors: list[str] = []
    if reproduction["compatible"] is not True:
        errors.append("genome did not reproduce within its declared bound")
    if archive["automaticActivationAuthorized"] is not False or len(archive["crossModelDomains"]) != 2:
        errors.append("genome archive activation or cross-domain contract is invalid")
    if promotion["status"] != "PROMOTED_AUTONOMOUS_THRESHOLD_PASS" or promotion["authorityGranted"] is not False:
        errors.append("higher-tier memory promotion did not pass the autonomous threshold gate")
    if promotion["automaticPromotionAuthorized"] is not True or promotion["operatorPromotionRequired"] is not False:
        errors.append("higher-tier memory promotion retained a manual path or failed to activate its projection")
    if expiry["direction"] != "DEMOTE_OR_EXPIRE" or expiry["reversible"] is not True:
        errors.append("expired memory did not demote reversibly")
    if blocked_schedule["selected"] != [] or blocked_schedule["reputationCanBypassGates"] is not False:
        errors.append("reputation bypassed a hard scheduler gate")
    if unsigned_admission["status"] != "REJECTED" or tampered_admission["status"] != "REJECTED":
        errors.append("unsigned or tampered capsule was not rejected")
    if valid_admission["status"] != "QUARANTINED_ELIGIBLE_FOR_LOCAL_REVIEW":
        errors.append("valid capsule did not remain quarantined")
    if any(valid_admission[key] is not False for key in ("authorityTransferred", "credentialsTransferred", "privateMemoryTransferred", "approvalTransferred", "automaticActivation")):
        errors.append("valid capsule transferred a prohibited capability")
    if any(federation[key] is not None for key in ("credentials", "privateMemory", "approval")) or federation["authorityTransferred"] is not False:
        errors.append("federation envelope transferred private state or authority")
    write_json(output_root / "genome-archive.json", archive)
    write_json(output_root / "genome-reproduction.json", reproduction)
    write_json(output_root / "memory-promotion.json", promotion)
    write_json(output_root / "memory-expiry.json", expiry)
    write_json(output_root / "reputation-blocked-schedule.json", blocked_schedule)
    write_json(output_root / "valid-capsule-admission.json", valid_admission)
    write_json(output_root / "unsigned-capsule-admission.json", unsigned_admission)
    write_json(output_root / "tampered-capsule-admission.json", tampered_admission)
    write_json(output_root / "federation-envelope.json", federation)
    summary_without_digest: dict[str, object] = {
        "schema": PROOF_SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "root": str(root),
        "genomeCount": 2,
        "crossModelDomainEvaluationCount": 2,
        "genomeReproducedWithinBound": reproduction["compatible"],
        "memoryPromotionStatus": promotion["status"],
        "expiredMemoryDisposition": expiry["direction"],
        "reputationHardGateStatus": blocked_schedule["status"],
        "unsignedCapsuleStatus": unsigned_admission["status"],
        "tamperedCapsuleStatus": tampered_admission["status"],
        "validCapsuleStatus": valid_admission["status"],
        "providerCalls": 0,
        "errors": errors,
        "authority": {
            "reputationBypass": False,
            "remoteAuthorityTransferred": False,
            "credentialsTransferred": False,
            "privateMemoryTransferred": False,
            "approvalTransferred": False,
            "automaticActivation": False,
            "memoryPromotedProjectionActivation": promotion["promotedProjectionActivationAuthorized"],
        },
    }
    summary = summary_without_digest | {"proofSha256": digest(json.dumps(summary_without_digest, sort_keys=True))}
    write_json(output_root / "epoch-five-summary.json", summary)
    if errors:
        raise RuntimeError(f"Epoch Five cognitive-ecology proof failed: {errors}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove Blunder Epoch Five cognitive ecology.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--output-root", required=True)
    arguments = parser.parse_args()
    summary = build_proof(Path(arguments.root).resolve(), Path(arguments.output_root).resolve())
    print(json.dumps(summary, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
