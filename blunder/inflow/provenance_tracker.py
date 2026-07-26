from __future__ import annotations

from pathlib import Path
import json

from blunder.inflow.models import SourceCandidate, utc_now


def write_source_manifest(artifact_root: Path, candidate: SourceCandidate, content_hash: str) -> Path:
    manifest_dir = artifact_root / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    output = manifest_dir / f"{content_hash}.manifest.json"
    payload = {
        "source_uri": candidate["source_uri"],
        "source_type": candidate["source_type"],
        "trust_class": candidate["trust_class"],
        "license_class": candidate["license_class"],
        "content_hash": content_hash,
        "recorded_at": utc_now(),
        "external_effects": False,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return output
