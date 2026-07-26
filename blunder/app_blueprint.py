from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


BLUEPRINT_SCHEMA = "obtuse.blunder.app-blueprint.v1"
ALLOWED_PLATFORMS: frozenset[str] = frozenset(
    {"web", "ios", "android", "desktop", "game", "console"}
)
REQUIRED_EXPERIENCE_STATES: tuple[str, ...] = (
    "initial",
    "loading",
    "ready",
    "empty",
    "error",
    "offline",
    "permission-denied",
)


@dataclass(frozen=True)
class BlueprintRequest:
    product_name: str
    goal: str
    audience: tuple[str, ...]
    platforms: tuple[str, ...]
    capabilities: tuple[str, ...]
    entities: tuple[str, ...]
    constraints: tuple[str, ...]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized_identifier(value: str) -> str:
    observed = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not observed:
        raise ValueError("Product identifiers require at least one letter or number.")
    return observed[:80]


def required_text(value: Mapping[str, object], key: str, minimum: int, maximum: int) -> str:
    observed = str(value.get(key) or "").strip()
    if len(observed) < minimum or len(observed) > maximum:
        raise ValueError(f"Blueprint {key} must contain from {minimum} through {maximum} characters.")
    return observed


def string_tuple(
    value: object,
    key: str,
    minimum: int,
    maximum: int,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"Blueprint {key} must be a JSON list.")
    observed = tuple(str(item).strip() for item in value)
    if not minimum <= len(observed) <= maximum:
        raise ValueError(f"Blueprint {key} must contain from {minimum} through {maximum} values.")
    if any(not item for item in observed) or len(set(item.lower() for item in observed)) != len(observed):
        raise ValueError(f"Blueprint {key} contains an empty or duplicate value.")
    return observed


def parse_request(value: Mapping[str, object]) -> BlueprintRequest:
    platforms = tuple(item.lower() for item in string_tuple(value.get("platforms"), "platforms", 1, 6))
    unknown = frozenset(platforms).difference(ALLOWED_PLATFORMS)
    if unknown:
        raise ValueError(f"Blueprint platforms are unsupported: {', '.join(sorted(unknown))}")
    return BlueprintRequest(
        product_name=required_text(value, "productName", 2, 80),
        goal=required_text(value, "goal", 12, 1200),
        audience=string_tuple(value.get("audience"), "audience", 1, 8),
        platforms=platforms,
        capabilities=string_tuple(value.get("capabilities"), "capabilities", 1, 20),
        entities=string_tuple(value.get("entities"), "entities", 0, 20),
        constraints=string_tuple(value.get("constraints"), "constraints", 0, 20),
    )


def product_domain(platforms: tuple[str, ...]) -> str:
    observed = frozenset(platforms)
    if observed.intersection({"game", "console"}):
        return "game"
    if observed.intersection({"ios", "android"}):
        return "mobile"
    if "web" in observed:
        return "web"
    return "desktop"


def surface_contract(capability: str, sequence: int) -> dict[str, object]:
    identifier = normalized_identifier(capability)
    return {
        "id": f"surface-{sequence:02d}-{identifier}",
        "name": capability,
        "purpose": f"Let the user complete: {capability}",
        "states": list(REQUIRED_EXPERIENCE_STATES),
        "entryEvidence": "explicit user action or documented navigation edge",
        "successEvidence": "observable outcome with recoverable persisted state",
        "destructiveConfirmationRequired": bool(
            re.search(r"\b(?:delete|remove|purchase|pay|publish|submit|send)\b", capability, re.IGNORECASE)
        ),
    }


def journey_contract(capability: str, audience: tuple[str, ...], sequence: int) -> dict[str, object]:
    return {
        "id": f"journey-{sequence:02d}-{normalized_identifier(capability)}",
        "actor": audience[sequence % len(audience)],
        "outcome": capability,
        "steps": [
            "enter through a discoverable starting point",
            f"complete {capability} with visible system feedback",
            "verify the result and recover from failure without losing context",
        ],
        "failureBranches": ["offline", "invalid-input", "permission-denied", "service-failure"],
        "measurement": "completion rate, recovery rate, elapsed time, and intervention count",
    }


def quality_budgets(domain: str) -> dict[str, object]:
    universal: dict[str, object] = {
        "accessibilityCriticalViolations": 0,
        "layoutOverflowCount": 0,
        "uncaughtErrors": 0,
        "requiredExperienceStates": list(REQUIRED_EXPERIENCE_STATES),
        "minimumDeviceProfiles": 4,
    }
    domain_values: dict[str, dict[str, object]] = {
        "web": {"lcpMilliseconds": 2500, "inpMilliseconds": 200, "cls": 0.1},
        "mobile": {"coldStartMilliseconds": 2500, "droppedFramePercent": 1.0, "crashFreeSessionsPercent": 99.5},
        "game": {"targetFramesPerSecond": 60, "p95FrameMilliseconds": 16.67, "deterministicReplayRequired": True},
        "desktop": {"coldStartMilliseconds": 2000, "p95InteractionMilliseconds": 100},
    }
    return universal | domain_values[domain]


def compile_blueprint(value: Mapping[str, object]) -> dict[str, object]:
    request = parse_request(value)
    domain = product_domain(request.platforms)
    surfaces = [surface_contract(item, index) for index, item in enumerate(request.capabilities, start=1)]
    if len({str(item["id"]) for item in surfaces}) != len(surfaces):
        raise ValueError("Blueprint capabilities collapse to duplicate product surface identifiers.")
    journeys = [
        journey_contract(item, request.audience, index)
        for index, item in enumerate(request.capabilities, start=1)
    ]
    navigation_edges = [
        {
            "from": surfaces[index - 1]["id"] if index > 0 else "launch",
            "to": surface["id"],
            "condition": "user intent remains valid",
            "backBehavior": "restore prior stable state",
        }
        for index, surface in enumerate(surfaces)
    ]
    blueprint_id = hashlib.sha256(
        json.dumps(asdict(request), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16].upper()
    return {
        "schema": BLUEPRINT_SCHEMA,
        "id": f"blueprint-{blueprint_id}",
        "createdAt": utc_now(),
        "status": "READY_FOR_PRODUCT_PLANNING",
        "request": asdict(request),
        "domain": domain,
        "surfaces": surfaces,
        "journeys": journeys,
        "navigation": navigation_edges,
        "dataModel": [
            {
                "entity": entity,
                "id": normalized_identifier(entity),
                "ownership": "unresolved-requires-product-decision",
                "retention": "unresolved-requires-product-decision",
                "offlinePolicy": "explicit-conflict-policy-required",
            }
            for entity in request.entities
        ],
        "qualityBudgets": quality_budgets(domain),
        "acceptance": [
            "every declared journey has executable happy-path and recovery proof",
            "every surface renders all required experience states",
            "device, accessibility, visual, performance, and security budgets pass",
            "release evidence matches the exact source revision",
        ],
        "openDecisions": [
            *(f"define ownership, retention, and conflict policy for {entity}" for entity in request.entities),
            *(() if request.constraints else ("define explicit delivery constraints",)),
            *(() if request.entities else ("define the product data entities",)),
        ],
        "authority": {
            "sourceMutation": False,
            "providerCalls": 0,
            "deployment": False,
            "storeSubmission": False,
            "publicRelease": False,
        },
    }


def write_blueprint(path: Path, blueprint: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(blueprint), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path
