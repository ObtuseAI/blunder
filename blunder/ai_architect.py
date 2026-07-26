from __future__ import annotations

from typing import Mapping

from blunder.creative_direction import canonical_digest, required_text, text_sequence


AI_ARCHITECTURE_SCHEMA = "obtuse.blunder.ai-system-architecture.v1"


def compile_ai_architecture(value: Mapping[str, object]) -> dict[str, object]:
    context = "AI architecture"
    name = required_text(value, "name", context, 100)
    outcome = required_text(value, "outcome", context, 1200)
    decisions = text_sequence(value.get("userDecisions"), "userDecisions", context, 1, 12)
    inputs = text_sequence(value.get("inputs"), "inputs", context, 1, 16)
    outputs = text_sequence(value.get("outputs"), "outputs", context, 1, 16)
    tools = text_sequence(value.get("tools"), "tools", context, 0, 24)
    risks = text_sequence(value.get("risks"), "risks", context, 1, 16)
    report: dict[str, object] = {
        "schema": AI_ARCHITECTURE_SCHEMA,
        "status": "READY_FOR_AI_SYSTEM_REVIEW",
        "name": name,
        "outcome": outcome,
        "decisionContract": [
            {"decision": decision, "owner": "user", "automationRole": "advise-or-draft", "silentAutomation": False}
            for decision in decisions
        ],
        "topology": {
            "inputBoundary": [{"name": item, "trust": "untrusted-until-validated"} for item in inputs],
            "contextPlane": ["task state", "retrieved evidence", "policy", "budget", "recent trace"],
            "reasoningPlane": ["planner", "specialist", "skeptic", "deterministic judge"],
            "toolPlane": [{"name": item, "schemaRequired": True, "leastPrivilege": True} for item in tools],
            "outputBoundary": [{"name": item, "schemaRequired": True, "provenanceRequired": True} for item in outputs],
            "memoryPlane": ["ephemeral turn context", "content-addressed task state", "operator-approved durable learning"],
        },
        "experienceContract": {
            "onboarding": ["state user benefit", "show an example", "state limits", "explain how feedback changes behavior"],
            "uncertainty": ["show confidence only when calibrated", "separate observation from inference", "name missing evidence"],
            "control": ["preview consequential actions", "require approval at risk boundaries", "provide manual control", "support correction and undo"],
            "failure": ["preserve user work", "name the failed layer", "offer a manual path", "never fabricate completion"],
        },
        "riskRegister": [
            {"risk": risk, "prevent": "typed boundary and least privilege", "detect": "trace grader", "recover": "checkpoint and explicit blocked state"}
            for risk in risks
        ],
        "evaluationContract": {
            "offline": ["golden tasks", "adversarial inputs", "tool-contract tests", "calibration cases"],
            "trace": ["decision quality", "tool choice", "policy adherence", "recovery quality"],
            "product": ["task completion", "correction rate", "trust calibration", "time to recovery"],
            "promotion": "No model, prompt, or workflow promotion without repeatable regression evidence.",
        },
        "openInterfaces": ["JSON Schema", "MCP-compatible tool descriptions", "OpenTelemetry-compatible trace fields"],
        "providerCalls": 0,
        "sourceMutation": False,
    }
    report["digest"] = canonical_digest(report)
    return report
