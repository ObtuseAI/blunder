from __future__ import annotations

import hashlib
import json
from typing import Mapping, Sequence


CREATIVE_BRIEF_SCHEMA = "obtuse.blunder.creative-brief.v1"
CREATIVE_ARCHETYPES: tuple[Mapping[str, object], ...] = (
    {
        "id": "precision",
        "thesis": "Make the difficult feel inevitable through exact hierarchy and restrained detail.",
        "visualGrammar": ["high information contrast", "quiet geometry", "one decisive accent", "visible evidence"],
        "interaction": ["progressive disclosure", "keyboard-first flow", "context beside action"],
        "voice": "direct, calm, and exact",
    },
    {
        "id": "kinetic",
        "thesis": "Turn progress into a legible rhythm that makes complex work feel alive.",
        "visualGrammar": ["directional composition", "temporal layers", "bold scale shifts", "motion with purpose"],
        "interaction": ["immediate response", "spatial continuity", "reduced-motion equivalent"],
        "voice": "energetic, economical, and confident",
    },
    {
        "id": "atelier",
        "thesis": "Treat the product as a crafted instrument whose material character rewards attention.",
        "visualGrammar": ["tactile surfaces", "editorial rhythm", "authored imperfection", "deep focal moments"],
        "interaction": ["direct manipulation", "reversible exploration", "deliberate reveals"],
        "voice": "warm, curious, and distinctive",
    },
    {
        "id": "signal",
        "thesis": "Reveal the hidden system through luminous signals, topology, and consequence.",
        "visualGrammar": ["semantic color", "network motifs", "dense-to-clear transitions", "ambient status"],
        "interaction": ["traceable causality", "state previews", "evidence trails"],
        "voice": "technical without jargon, vivid without spectacle",
    },
    {
        "id": "civic",
        "thesis": "Build trust through clarity, legibility, and controls that visibly respect the user.",
        "visualGrammar": ["generous structure", "plain-language labels", "stable landmarks", "inclusive defaults"],
        "interaction": ["explicit consent", "undo everywhere", "graceful failure"],
        "voice": "human, candid, and reassuring",
    },
    {
        "id": "organic",
        "thesis": "Let the interface adapt like a living system while keeping every change understandable.",
        "visualGrammar": ["soft topology", "adaptive density", "natural cadence", "layered depth"],
        "interaction": ["co-learning", "preference shaping", "explainable adaptation"],
        "voice": "observant, patient, and imaginative",
    },
)
SIGNATURE_MOVES: Mapping[str, str] = {
    "precision": "Turn the decisive state into one exact instrument with proof aligned beside every control.",
    "kinetic": "Make progress legible as a spatial sequence whose motion always explains causality.",
    "atelier": "Give the decisive state a crafted focal artifact that users can directly shape and revisit.",
    "signal": "Reveal inputs, transformations, uncertainty, and effects as one traceable signal path.",
    "civic": "Make authority, consent, consequence, and recovery unmistakable at the decisive state.",
    "organic": "Let the decisive state adapt to feedback while visibly explaining what changed and why.",
}


def required_text(value: Mapping[str, object], key: str, context: str, maximum: int) -> str:
    observed = str(value.get(key) or "").strip()
    if not observed or len(observed) > maximum:
        raise ValueError(f"{context} requires {key} with at most {maximum} characters.")
    return observed


def text_sequence(value: object, key: str, context: str, minimum: int, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{context} {key} must be a list.")
    observed = tuple(str(item).strip() for item in value)
    if len(observed) < minimum or len(observed) > maximum or any(not item for item in observed):
        raise ValueError(f"{context} {key} must contain {minimum} through {maximum} non-empty values.")
    if len(set(item.casefold() for item in observed)) != len(observed):
        raise ValueError(f"{context} {key} contains duplicate values.")
    return observed


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def selected_archetypes(seed: str, count: int) -> tuple[Mapping[str, object], ...]:
    if count < 2 or count > len(CREATIVE_ARCHETYPES):
        raise ValueError("Creative direction count must be from 2 through 6.")
    offset = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) % len(CREATIVE_ARCHETYPES)
    stride = 5
    return tuple(CREATIVE_ARCHETYPES[(offset + index * stride) % len(CREATIVE_ARCHETYPES)] for index in range(count))


def direction_record(
    archetype: Mapping[str, object],
    outcome: str,
    principles: Sequence[str],
    avoid: Sequence[str],
    audience: str,
    moment: str,
    index: int,
) -> dict[str, object]:
    identifier = str(archetype["id"])
    record: dict[str, object] = {
        "id": f"direction-{index + 1}-{identifier}",
        "name": identifier.replace("-", " ").title(),
        "thesis": str(archetype["thesis"]),
        "outcomeAnchor": outcome,
        "visualGrammar": list(archetype["visualGrammar"]),
        "interactionGrammar": list(archetype["interaction"]),
        "voice": str(archetype["voice"]),
        "principleEmphasis": principles[index % len(principles)],
        "antiPatterns": list(avoid),
        "signatureMove": f"For {audience} during '{moment}': {SIGNATURE_MOVES[identifier]}",
        "prototypePlan": {
            "lowFidelity": f"Storyboard the '{moment}' decision and recovery path before styling.",
            "highFidelity": f"Build one keyboard-complete {identifier} prototype using open tokens and real state data.",
            "evidence": ["first-action comprehension", "task completion", "correction path", "accessibility review", "distinctiveness recall"],
        },
        "prototypeQuestion": f"Does the {identifier} direction make the primary outcome faster to understand and safer to complete?",
    }
    record["digest"] = canonical_digest(record)
    return record


def storyboard(moment: str, audience: str, index: int) -> dict[str, object]:
    return {
        "id": f"moment-{index + 1}",
        "moment": moment,
        "audience": audience,
        "beats": [
            {"stage": "orient", "proof": "The user can state what is possible, what is not, and what happens next."},
            {"stage": "act", "proof": "The primary action is obvious, reversible, and free of hidden authority."},
            {"stage": "understand", "proof": "Progress, uncertainty, and system reasoning are visible at the useful level."},
            {"stage": "recover", "proof": "Failure preserves work and offers one concrete route forward."},
        ],
        "requiredStates": ["empty", "loading", "ready", "partial", "uncertain", "blocked", "error", "success"],
    }


def compile_creative_brief(value: Mapping[str, object]) -> dict[str, object]:
    context = "Creative brief"
    name = required_text(value, "name", context, 100)
    outcome = required_text(value, "outcome", context, 1200)
    audiences = text_sequence(value.get("audiences"), "audiences", context, 1, 8)
    moments = text_sequence(value.get("moments"), "moments", context, 1, 12)
    principles = text_sequence(value.get("principles"), "principles", context, 2, 8)
    avoid = text_sequence(value.get("avoid"), "avoid", context, 1, 8)
    raw_count = value.get("directionCount")
    if not isinstance(raw_count, int) or isinstance(raw_count, bool):
        raise ValueError("Creative brief directionCount must be an integer.")
    directions = tuple(
        direction_record(archetype, outcome, principles, avoid, audiences[index % len(audiences)], moments[index % len(moments)], index)
        for index, archetype in enumerate(selected_archetypes(f"{name}:{outcome}", raw_count))
    )
    storyboards = tuple(storyboard(moment, audiences[index % len(audiences)], index) for index, moment in enumerate(moments))
    report: dict[str, object] = {
        "schema": CREATIVE_BRIEF_SCHEMA,
        "status": "READY_FOR_CREATIVE_REVIEW",
        "name": name,
        "outcome": outcome,
        "audiences": list(audiences),
        "principles": list(principles),
        "directions": list(directions),
        "storyboards": list(storyboards),
        "interfaceContract": {
            "hierarchy": ["one primary decision per surface", "evidence adjacent to consequential action", "advanced state progressively disclosed"],
            "accessibility": ["keyboard complete", "semantic structure", "visible focus", "reduced motion", "zoom-safe reflow", "non-color status cues"],
            "aiExperience": ["state capability and limits", "show consequential uncertainty", "connect feedback to effect", "provide manual control", "fail with a recovery path"],
            "designTokenFormat": "DTCG-compatible open JSON",
        },
        "selectionPolicy": "Preserve multiple meaningfully different directions until prototype evidence exists; no aesthetic champion is declared from the brief alone.",
        "providerCalls": 0,
        "sourceMutation": False,
    }
    report["digest"] = canonical_digest(report)
    return report
