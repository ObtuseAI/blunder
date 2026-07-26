from __future__ import annotations

import math
from typing import Mapping, Sequence

from blunder.creative_direction import canonical_digest, required_text


TRACE_EVALUATION_SCHEMA = "obtuse.blunder.trace-evaluation.v1"
CREATIVE_ARCHIVE_SCHEMA = "obtuse.blunder.creative-quality-archive.v1"
TRACE_KINDS = frozenset({"input", "model", "tool", "handoff", "guardrail", "human", "output", "error"})


def trace_span(value: Mapping[str, object]) -> dict[str, object]:
    span_id = required_text(value, "id", "Trace span", 100)
    kind = str(value.get("kind") or "").lower()
    if kind not in TRACE_KINDS:
        raise ValueError(f"Trace span {span_id} has unsupported kind {kind}.")
    duration = float(value.get("durationMs") or 0)
    tokens = int(value.get("tokens") or 0)
    cost = float(value.get("costUsd") or 0)
    if duration < 0 or tokens < 0 or cost < 0:
        raise ValueError(f"Trace span {span_id} has negative usage.")
    return {
        "id": span_id,
        "parentId": str(value.get("parentId") or "") or None,
        "kind": kind,
        "name": required_text(value, "name", f"Trace span {span_id}", 160),
        "status": str(value.get("status") or "unknown").lower(),
        "durationMs": duration,
        "tokens": tokens,
        "costUsd": cost,
        "toolApproved": value.get("toolApproved") is True,
        "grounded": value.get("grounded") is True,
    }


def evaluate_trace(value: Mapping[str, object]) -> dict[str, object]:
    trace_id = required_text(value, "traceId", "Trace", 100)
    raw_spans = value.get("spans")
    if not isinstance(raw_spans, list) or not raw_spans or any(not isinstance(item, dict) for item in raw_spans):
        raise ValueError(f"Trace {trace_id} spans must be a non-empty object list.")
    spans = tuple(trace_span(item) for item in raw_spans if isinstance(item, dict))
    identifiers = frozenset(str(item["id"]) for item in spans)
    if len(identifiers) != len(spans):
        raise ValueError(f"Trace {trace_id} contains duplicate span ids.")
    orphaned = tuple(str(item["id"]) for item in spans if item["parentId"] is not None and item["parentId"] not in identifiers)
    parents = {str(item["id"]): str(item["parentId"]) for item in spans if item["parentId"] is not None}
    cyclic: list[str] = []
    for span_id in identifiers:
        lineage: set[str] = set()
        current = span_id
        while current in parents:
            if current in lineage:
                cyclic.append(span_id)
                break
            lineage.add(current)
            current = parents[current]
    roots = tuple(str(item["id"]) for item in spans if item["parentId"] is None)
    unapproved_tools = tuple(str(item["id"]) for item in spans if item["kind"] == "tool" and not item["toolApproved"])
    ungrounded_outputs = tuple(str(item["id"]) for item in spans if item["kind"] == "output" and not item["grounded"])
    errors = tuple(str(item["id"]) for item in spans if item["kind"] == "error" or item["status"] == "error")
    gates = {
        "structure": not orphaned and not cyclic and len(roots) == 1,
        "toolApproval": not unapproved_tools,
        "groundedOutput": not ungrounded_outputs,
        "terminalSuccess": any(item["kind"] == "output" and item["status"] == "completed" for item in spans),
        "noUnhandledErrors": not errors,
    }
    report: dict[str, object] = {
        "schema": TRACE_EVALUATION_SCHEMA,
        "status": "PASS" if all(gates.values()) else "FAIL",
        "traceId": trace_id,
        "gates": gates,
        "findings": {"roots": list(roots), "orphaned": list(orphaned), "cyclic": sorted(set(cyclic)), "unapprovedTools": list(unapproved_tools), "ungroundedOutputs": list(ungrounded_outputs), "errors": list(errors)},
        "usage": {
            "durationMs": round(sum(float(item["durationMs"]) for item in spans), 3),
            "tokens": sum(int(item["tokens"]) for item in spans),
            "costUsd": round(sum(float(item["costUsd"]) for item in spans), 6),
        },
        "spans": list(spans),
        "promotionEligible": all(gates.values()),
    }
    report["digest"] = canonical_digest(report)
    return report


def normalized_score(value: Mapping[str, object], key: str) -> float:
    score = float(value.get(key) or 0)
    if not 0 <= score <= 1 or not math.isfinite(score):
        raise ValueError(f"Creative candidate {key} must be a finite score from 0 through 1.")
    return score


def archive_creative_candidates(values: Sequence[Mapping[str, object]]) -> dict[str, object]:
    if not values:
        return {
            "schema": CREATIVE_ARCHIVE_SCHEMA,
            "status": "NO_CANDIDATES",
            "archive": [],
            "coverage": 0.0,
            "champion": None,
            "selectionPolicy": "No creative champion without measured candidates.",
        }
    archive: dict[tuple[int, int], dict[str, object]] = {}
    for value in values:
        candidate_id = required_text(value, "id", "Creative candidate", 100)
        novelty = normalized_score(value, "novelty")
        usefulness = normalized_score(value, "usefulness")
        coherence = normalized_score(value, "coherence")
        accessibility = normalized_score(value, "accessibility")
        evidence = str(value.get("evidence") or "").strip()
        if not evidence:
            raise ValueError(f"Creative candidate {candidate_id} requires evidence.")
        quality = round((usefulness * 0.4) + (coherence * 0.3) + (accessibility * 0.3), 6)
        cell = (min(3, int(novelty * 4)), min(3, int(coherence * 4)))
        record = {
            "id": candidate_id,
            "cell": {"noveltyBand": cell[0], "coherenceBand": cell[1]},
            "novelty": novelty,
            "usefulness": usefulness,
            "coherence": coherence,
            "accessibility": accessibility,
            "quality": quality,
            "evidence": evidence,
        }
        incumbent = archive.get(cell)
        if incumbent is None or (quality, novelty, candidate_id) > (float(incumbent["quality"]), float(incumbent["novelty"]), str(incumbent["id"])):
            archive[cell] = record
    records = sorted(archive.values(), key=lambda item: (-float(item["quality"]), -float(item["novelty"]), str(item["id"])))
    champion = records[0] if records and float(records[0]["quality"]) >= 0.8 and float(records[0]["accessibility"]) >= 0.8 else None
    return {
        "schema": CREATIVE_ARCHIVE_SCHEMA,
        "status": "CHAMPION_AVAILABLE_FOR_REVIEW" if champion is not None else "DIVERSE_CANDIDATES_REQUIRE_MORE_PROOF",
        "archive": records,
        "coverage": round(len(records) / 16, 4),
        "champion": champion,
        "selectionPolicy": "Keep the highest evidenced quality candidate in each novelty/coherence cell; accessibility remains a champion gate.",
    }
