from __future__ import annotations

from pathlib import Path
import json

from blunder.inflow.models import BlunderInflowRecord


VISUAL_SUFFIXES: set[str] = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".pdf"}


def classify_visual_evidence(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return ["VISUAL_LAYOUT_REQUIRED", "TABLE_STRUCTURE_LOST", "DIAGRAM_SEMANTICS_REQUIRED"]
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp"}:
        return ["SCREENSHOT_ONLY_EVIDENCE", "TEXT_VISUAL_CONFLICT"]
    return []


def ingest_visual_evidence(artifact_root: Path, record: BlunderInflowRecord, mutate: bool) -> BlunderInflowRecord:
    if not record["raw_artifact_path"]:
        return record
    raw_path = Path(record["raw_artifact_path"])
    if raw_path.suffix.lower() not in VISUAL_SUFFIXES:
        return record
    labels = classify_visual_evidence(raw_path)
    payload = {
        "source": str(raw_path),
        "tiles": [{"tile_id": f"{record['record_id']}-tile-000", "bbox": [0, 0, 1, 1], "labels": labels}],
        "parser_loss_labels": labels,
        "ocr_trusted_alone": False,
    }
    if mutate:
        output_dir = artifact_root / "visual_tiles"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"{record['record_id']}.json"
        output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        record["visual_tiles_path"] = str(output)
    record["validation_refs"].append("VISUAL_EVIDENCE_RECORD_CREATED")
    return record
