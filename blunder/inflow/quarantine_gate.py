from __future__ import annotations

from pathlib import Path
import json

from blunder.inflow.content_hasher import hash_bytes
from blunder.inflow.license_gate import classify_license, is_license_compatible
from blunder.inflow.models import BlunderInflowRecord, SourceCandidate, build_record
from blunder.inflow.provenance_tracker import write_source_manifest
from blunder.inflow.secret_sentinel import scan_path_for_risk, scan_text_for_risk
from blunder.inflow.source_trust_scorer import compute_risk_score, compute_trust_score


def _read_candidate(candidate: SourceCandidate) -> tuple[bytes, str, str]:
    path_text = candidate.get("path", "")
    inline_text = candidate.get("inline_text", "")
    if path_text:
        path = Path(path_text)
        data = path.read_bytes()
        text = data.decode("utf-8", errors="replace")
        suffix = path.suffix if path.suffix else ".txt"
        return data, text, suffix
    data = inline_text.encode("utf-8", errors="replace")
    return data, inline_text, ".txt"


def quarantine_source(
    artifact_root: Path,
    candidate: SourceCandidate,
    known_hashes: set[str],
    mutate: bool,
) -> tuple[BlunderInflowRecord, dict[str, object] | None]:
    data, text, suffix = _read_candidate(candidate)
    content_hash = hash_bytes(data)
    risk_flags = scan_text_for_risk(text)
    path_text = candidate.get("path", "")
    if path_text:
        risk_flags.extend(scan_path_for_risk(Path(path_text)))
    actual_license = classify_license(text, candidate["license_class"])
    duplication_flags = ["DUPLICATE_CONTENT_HASH"] if content_hash in known_hashes else []
    known_hashes.add(content_hash)
    trust_score = compute_trust_score(candidate, risk_flags, 0)
    risk_score = compute_risk_score(risk_flags, actual_license)
    promotion_status = "quarantined"
    rejection: dict[str, object] | None = None
    if risk_flags or not is_license_compatible(actual_license):
        promotion_status = "rejected"
        rejection = {
            "source_uri": candidate["source_uri"],
            "content_hash": content_hash,
            "risk_flags": risk_flags,
            "license_class": actual_license,
            "reason": "RISK_OR_LICENSE_GATE_FAILED",
        }
    raw_path = ""
    if mutate:
        raw_dir = artifact_root / "quarantine" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_file = raw_dir / f"{content_hash}{suffix}"
        raw_file.write_bytes(data)
        write_source_manifest(artifact_root, candidate, content_hash)
        raw_path = str(raw_file)
    record = build_record(
        candidate,
        content_hash,
        trust_score,
        risk_score,
        raw_path,
        "",
        "",
        "",
        "",
        risk_flags,
        duplication_flags,
        promotion_status,
    )
    record["license_class"] = actual_license
    record["paopvol"] = {
        "problem": "Source requires proof-native quarantine before any learning use.",
        "attempt": "Content hashed, classified, risk scanned, and license scanned.",
        "observation": json.dumps({"risk_flags": risk_flags, "duplicate": bool(duplication_flags)}, sort_keys=True),
        "patch": "No active knowledge mutation; record remains quarantined or rejected.",
        "validation": "Quarantine gate completed.",
        "outcome": promotion_status,
        "lesson": "Raw inflow never promotes directly.",
    }
    return record, rejection
