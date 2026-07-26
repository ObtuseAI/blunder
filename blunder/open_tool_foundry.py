from __future__ import annotations

import re
from typing import Mapping

from blunder.creative_direction import canonical_digest, required_text, text_sequence


TOOL_SCHEMA = "obtuse.blunder.open-tool-contract.v1"
TOOL_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
PERMISSIONS = frozenset({
    "source:read", "source:search", "source:write-bounded", "artifact:read", "artifact:write",
    "tests:run", "network:read", "network:write-approved", "runtime:read", "runtime:bounded",
    "human:request", "secrets:reference", "queue:read", "queue:write-bounded",
})


def schema_object(value: object, key: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or value.get("type") != "object":
        raise ValueError(f"Open tool {key} must be a JSON Schema object.")
    properties = value.get("properties")
    if not isinstance(properties, dict):
        raise ValueError(f"Open tool {key} requires a properties object.")
    return dict(value) | {"additionalProperties": False}


def compile_tool_contract(value: Mapping[str, object]) -> dict[str, object]:
    name = required_text(value, "name", "Open tool", 64)
    if TOOL_NAME.fullmatch(name) is None:
        raise ValueError("Open tool name must use lower-case letters, numbers, underscores, or hyphens.")
    description = required_text(value, "description", f"Open tool {name}", 500)
    permissions = text_sequence(value.get("permissions"), "permissions", f"Open tool {name}", 1, 12)
    unknown = sorted(set(permissions).difference(PERMISSIONS))
    if unknown:
        raise ValueError(f"Open tool {name} uses unknown permissions: {', '.join(unknown)}")
    risk = str(value.get("risk") or "").lower()
    if risk not in {"read-only", "bounded-write", "external-effect"}:
        raise ValueError(f"Open tool {name} has unsupported risk {risk}.")
    approval = str(value.get("approval") or "").lower()
    if approval not in {"never", "on-write", "always"}:
        raise ValueError(f"Open tool {name} has unsupported approval policy {approval}.")
    if risk == "external-effect" and approval != "always":
        raise ValueError(f"Open tool {name} external effects require approval=always.")
    input_schema = schema_object(value.get("inputSchema"), "inputSchema")
    output_schema = schema_object(value.get("outputSchema"), "outputSchema")
    idempotency = str(value.get("idempotency") or "").strip()
    if risk != "read-only" and not idempotency:
        raise ValueError(f"Open tool {name} requires idempotency for write or external-effect behavior.")
    timeout_seconds = value.get("timeoutSeconds")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
        raise ValueError(f"Open tool {name} timeoutSeconds must be an integer.")
    contract: dict[str, object] = {
        "schema": TOOL_SCHEMA,
        "status": "READY_FOR_IMPLEMENTATION",
        "name": name,
        "description": description,
        "inputSchema": dict(input_schema),
        "outputSchema": dict(output_schema),
        "permissions": list(permissions),
        "risk": risk,
        "approval": approval,
        "idempotency": idempotency or "read-only",
        "timeoutSeconds": timeout_seconds,
        "protocolViews": {
            "blunder": {"id": name, "permissions": list(permissions), "approval": approval},
            "mcp": {"name": name, "description": description, "inputSchema": dict(input_schema), "outputSchema": dict(output_schema)},
        },
        "implementationChecklist": ["validate every input", "bound output size", "redact secrets", "emit structured trace", "raise actionable errors", "test cancellation"],
        "runtimeInstalled": False,
        "providerCalls": 0,
        "sourceMutation": False,
    }
    if not 1 <= int(contract["timeoutSeconds"]) <= 3600:
        raise ValueError(f"Open tool {name} timeoutSeconds must be from 1 through 3600.")
    contract["digest"] = canonical_digest(contract)
    return contract
