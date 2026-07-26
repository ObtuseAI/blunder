from __future__ import annotations

from pathlib import Path

from blunder.inflow.models import BlunderInflowRecord


def detect_record_contradictions(record: BlunderInflowRecord) -> BlunderInflowRecord:
    text = ""
    if record["normalized_text_path"]:
        text = Path(record["normalized_text_path"]).read_text(encoding="utf-8", errors="replace").lower()
    flags: list[str] = []
    if "pass" in text and "fail" in text:
        flags.append("PASS_FAIL_MIXED_CLAIM")
    if "current" in text and "stale" in text:
        flags.append("CURRENT_STALE_MIXED_CLAIM")
    record["contradiction_flags"] = flags
    return record


def detect_cross_source_contradictions(records: list[BlunderInflowRecord]) -> list[dict[str, object]]:
    contradictions: list[dict[str, object]] = []
    pass_records = [record["record_id"] for record in records if any(ref.endswith("PASS") for ref in record["validation_refs"])]
    fail_records = [record["record_id"] for record in records if "PASS_FAIL_MIXED_CLAIM" in record["contradiction_flags"]]
    if pass_records and fail_records:
        contradictions.append({
            "contradiction_id": "cross-source-pass-fail-mixed",
            "pass_records": pass_records[:5],
            "mixed_records": fail_records[:5],
            "resolution": "Prefer replayable validation artifacts over text assertions.",
        })
    return contradictions
