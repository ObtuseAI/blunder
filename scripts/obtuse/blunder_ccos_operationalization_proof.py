from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from blunder.ccos_canary import (
    ACTIVATION_CANARY_CONFIRMATION,
    ROLLBACK_CANARY_CONFIRMATION,
    run_operational_canary,
)
from blunder.ccos_runtime import (
    RECOVERY_CONFIRMATION,
    append_runtime_record,
    inspect_runtime_recovery,
    load_runtime_config,
    recover_last_known_good,
    retention_report,
    runtime_state,
)
from blunder.cognitive_ecology import canonical_digest


PROOF_SCHEMA = "obtuse.blunder.ccos-operationalization-proof.v1"


def write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def live_tree_identity(root: Path) -> str:
    rows: list[dict[str, str]] = []
    excluded = {".git", ".pytest_cache", "__pycache__", "artifacts"}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if excluded.intersection(relative.parts) or path.suffix in {".pyc", ".pyo"}:
            continue
        rows.append({"path": relative.as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest().upper()})
    return canonical_digest(rows)


def build_proof(root: Path, output_root: Path) -> dict[str, object]:
    output_root.mkdir(parents=True, exist_ok=False)
    observed_at = datetime.now(timezone.utc)
    source_config = load_runtime_config(root, root / "configs" / "obtuse" / "blunder-ccos-runtime.json")
    canary_config = replace(source_config, artifact_root=output_root / "provider-free-canary-runtime")
    recovery_config = replace(source_config, artifact_root=output_root / "recovery-drill-runtime")

    live_tree_before = live_tree_identity(root)
    canary = run_operational_canary(
        canary_config,
        observed_at,
        ACTIVATION_CANARY_CONFIRMATION,
        ROLLBACK_CANARY_CONFIRMATION,
    )
    canary_state = runtime_state(canary_config)
    clean_recovery_inspection = inspect_runtime_recovery(canary_config, observed_at + timedelta(seconds=1))
    retention = retention_report(canary_config, observed_at + timedelta(days=91))

    first = append_runtime_record(
        recovery_config,
        "recovery",
        {"checkpoint": "last-known-good", "source": "operationalization-proof"},
        observed_at,
    )
    append_runtime_record(
        recovery_config,
        "recovery",
        {"checkpoint": "interrupted-next-generation", "source": "operationalization-proof"},
        observed_at + timedelta(microseconds=1),
    )
    (recovery_config.artifact_root / "index.json").write_text('{"interrupted":true}\n', encoding="utf-8")
    corrupt_inspection = inspect_runtime_recovery(recovery_config, observed_at + timedelta(seconds=1))
    recovery = recover_last_known_good(recovery_config, RECOVERY_CONFIRMATION)
    recovered_state = runtime_state(recovery_config)
    post_recovery_inspection = inspect_runtime_recovery(recovery_config, observed_at + timedelta(seconds=2))
    live_tree_after = live_tree_identity(root)

    errors: list[str] = []
    if canary["status"] != "PASS_AUTHORITY_CLOSED_NO_CHAMPION":
        errors.append("provider-free operational canary did not preserve authority-closed no-champion state")
    if canary_state["generation"] != 7 or canary_state["providerCalls"] != 0:
        errors.append("operational canary runtime did not persist all seven evidence planes with zero provider calls")
    if clean_recovery_inspection["status"] != "CLEAN":
        errors.append("fresh operational canary runtime contains interrupted-write evidence")
    if retention["candidateCount"] != 1 or retention["canonicalRecordsDeleted"] != 0:
        errors.append("retention proof did not identify only the aged canary projection while preserving canonical evidence")
    if corrupt_inspection["indexStatus"] != "INVALID_FAIL_CLOSED":
        errors.append("recovery inspection did not fail closed on an interrupted index")
    if recovery["status"] != "RECOVERED_LAST_KNOWN_GOOD" or recovery["headDigest"] != first["recordDigest"]:
        errors.append("explicit recovery did not restore the exact last-known-good index")
    if recovered_state["generation"] != 1 or post_recovery_inspection["validOrphanRecordDigests"] == []:
        errors.append("post-recovery inspection did not preserve the interrupted generation as orphan evidence")
    if live_tree_before != live_tree_after:
        errors.append("provider-free canary or recovery drill changed canonical source identity")

    summary_without_digest: dict[str, object] = {
        "schema": PROOF_SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "generatedAt": observed_at.isoformat().replace("+00:00", "Z"),
        "root": str(root),
        "canaryStatus": canary["status"],
        "heldOutEvaluation": canary["heldOutEvaluation"],
        "activationCanary": canary["activationCanary"],
        "rollbackCanary": canary["rollbackCanary"],
        "persistedEvidencePlaneCount": canary_state["generation"],
        "recoveryStatus": recovery["status"],
        "recoveredGeneration": recovered_state["generation"],
        "orphanEvidencePreserved": bool(post_recovery_inspection["validOrphanRecordDigests"]),
        "retentionCandidateCount": retention["candidateCount"],
        "canonicalRecordsDeleted": retention["canonicalRecordsDeleted"],
        "liveTreeChanged": live_tree_before != live_tree_after,
        "providerCalls": 0,
        "authorityGranted": False,
        "errors": errors,
    }
    summary = summary_without_digest | {"proofSha256": canonical_digest(summary_without_digest)}
    write_json(output_root / "operational-canary.json", canary)
    write_json(output_root / "operational-canary-state.json", canary_state)
    write_json(output_root / "retention-report.json", retention)
    write_json(output_root / "corrupt-index-inspection.json", corrupt_inspection)
    write_json(output_root / "recovery-result.json", recovery)
    write_json(output_root / "post-recovery-inspection.json", post_recovery_inspection)
    write_json(output_root / "ccos-operationalization-summary.json", summary)
    if errors:
        raise RuntimeError(f"CCOS operationalization proof failed: {errors}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove durable authority-closed Blunder CCOS operation.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--output-root", required=True)
    arguments = parser.parse_args()
    summary = build_proof(Path(arguments.root).resolve(), Path(arguments.output_root).resolve())
    print(json.dumps(summary, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
