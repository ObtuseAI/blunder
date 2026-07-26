from __future__ import annotations

from pathlib import Path
import json
import re

from blunder.inflow.models import BlunderInflowRecord


def normalize_text(text: str) -> str:
    without_control = "".join(ch if ch == "\n" or ch == "\t" or ord(ch) >= 32 else " " for ch in text)
    compact = re.sub(r"[ \t]+", " ", without_control)
    return compact.strip()


def normalize_record(artifact_root: Path, record: BlunderInflowRecord, mutate: bool) -> BlunderInflowRecord:
    if not record["raw_artifact_path"]:
        return record
    raw_path = Path(record["raw_artifact_path"])
    suffix = raw_path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".pdf"}:
        normalized = json.dumps({"visual_source": str(raw_path), "parser_loss": "VISUAL_LAYOUT_REQUIRED"}, sort_keys=True)
    else:
        normalized = normalize_text(raw_path.read_text(encoding="utf-8", errors="replace"))
    if mutate:
        output_dir = artifact_root / "normalized"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"{record['record_id']}.txt"
        output.write_text(normalized, encoding="utf-8")
        record["normalized_text_path"] = str(output)
    return record
