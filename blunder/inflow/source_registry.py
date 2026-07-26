from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from blunder.inflow.models import SourceCandidate


def _candidate(
    source_uri: str,
    source_type: str,
    trust_class: str,
    license_class: str,
    path: str,
    inline_text: str,
    scores: dict[str, float],
) -> SourceCandidate:
    candidate: SourceCandidate = {
        "source_uri": source_uri,
        "source_type": source_type,
        "trust_class": trust_class,
        "license_class": license_class,
        "authority_score": scores["authority_score"],
        "relevance_score": scores["relevance_score"],
        "freshness_score": scores["freshness_score"],
        "reproducibility_score": scores["reproducibility_score"],
        "validation_history_score": scores["validation_history_score"],
        "replayability_score": scores["replayability_score"],
        "internal_alignment_score": scores["internal_alignment_score"],
    }
    if path:
        candidate["path"] = path
    if inline_text:
        candidate["inline_text"] = inline_text
    return candidate


def build_source_registry(repo_root: Path) -> list[SourceCandidate]:
    high = {
        "authority_score": 1.0,
        "relevance_score": 1.0,
        "freshness_score": 0.9,
        "reproducibility_score": 0.95,
        "validation_history_score": 0.95,
        "replayability_score": 0.9,
        "internal_alignment_score": 1.0,
    }
    medium = {
        "authority_score": 0.78,
        "relevance_score": 0.82,
        "freshness_score": 0.72,
        "reproducibility_score": 0.72,
        "validation_history_score": 0.68,
        "replayability_score": 0.74,
        "internal_alignment_score": 0.78,
    }
    paths = [
        ("artifacts/obtuse/blunder-full-autonomy-uncorked-operations-v1/FINAL_REPORT.json", "proof_ledger", "TRUSTED_INTERNAL"),
        ("artifacts/obtuse/blunder-full-autonomy-uncorked-operations-v1/final_validation_summary.json", "validation_summary", "VALIDATION_ARTIFACT"),
        ("artifacts/obtuse/blunder-full-autonomy-uncorked-operations-v1/autonomy_bootstrap_summary.json", "runtime_telemetry", "RUNTIME_TELEMETRY"),
        ("artifacts/obtuse/blunder-dunce-import-and-benchmark-appliance-mode-v1/FINAL_REPORT.json", "dunce_experiment", "TRUSTED_INTERNAL"),
        ("artifacts/obtuse/autonomous-idle-dunce-takeover-v5-supervised-continuous-reliability-sla-bundle/idle-events.log", "terminal_log", "RUNTIME_TELEMETRY"),
        ("scripts/obtuse/blunder.ps1", "owned_workspace_file", "OWNED_WORKSPACE"),
    ]
    candidates: list[SourceCandidate] = []
    for relative_path, source_type, trust_class in paths:
        full = repo_root / relative_path
        if full.exists():
            score_set = high if trust_class in {"TRUSTED_INTERNAL", "VALIDATION_ARTIFACT"} else medium
            candidates.append(
                _candidate(
                    source_uri=str(full),
                    source_type=source_type,
                    trust_class=trust_class,
                    license_class="OWNED",
                    path=str(full),
                    inline_text="",
                    scores=score_set,
                )
            )
    official_text = json.dumps(
        {
            "source": "official-reference-placeholder",
            "rule": "Official references are recordable but not fetched by autonomous scraping.",
            "scheduler_containment": "No uncontrolled web access; current docs require explicit operator-approved fetch.",
        },
        sort_keys=True,
    )
    candidates.append(
        _candidate(
            source_uri="official://operator-approved-reference-placeholder",
            source_type="official_reference",
            trust_class="OFFICIAL_REFERENCE",
            license_class="OFFICIAL_REFERENCE",
            path="",
            inline_text=official_text,
            scores=medium,
        )
    )
    candidates.append(
        _candidate(
            source_uri="agent-trace://authorized-controlled-workflow-sample",
            source_type="agent_trace",
            trust_class="AGENT_TRACE",
            license_class="OWNED",
            path="",
            inline_text="Authorized agent trace sample: bad assumption detected, fixed, validated, and replay required before promotion.",
            scores=medium,
        )
    )
    candidates.append(
        _candidate(
            source_uri="quarantine://blocked-private-repo-marker-sample",
            source_type="agent_trace",
            trust_class="UNTRUSTED_QUARANTINE",
            license_class="UNKNOWN",
            path="",
            inline_text="Controlled blocked-source fixture: private repo marker and leaked code marker. This must be rejected.",
            scores=medium,
        )
    )
    return candidates


def write_source_registry(artifact_root: Path, candidates: list[SourceCandidate]) -> Path:
    output = artifact_root / "source_registry.json"
    output.write_text(json.dumps(candidates, indent=2, sort_keys=True), encoding="utf-8")
    return output
