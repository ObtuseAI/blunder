from __future__ import annotations

from typing import Mapping

from blunder.creative_direction import canonical_digest, required_text, text_sequence
from blunder.open_tool_foundry import schema_object


PROMPT_PROGRAM_SCHEMA = "obtuse.blunder.prompt-program.v1"


def compile_prompt_program(value: Mapping[str, object]) -> dict[str, object]:
    name = required_text(value, "name", "Prompt program", 100)
    objective = required_text(value, "objective", f"Prompt program {name}", 1200)
    instructions = text_sequence(value.get("instructions"), "instructions", f"Prompt program {name}", 2, 20)
    context_sources = text_sequence(value.get("contextSources"), "contextSources", f"Prompt program {name}", 1, 20)
    prohibited = text_sequence(value.get("prohibited"), "prohibited", f"Prompt program {name}", 1, 20)
    input_schema = schema_object(value.get("inputSchema"), "inputSchema")
    output_schema = schema_object(value.get("outputSchema"), "outputSchema")
    token_budget = value.get("tokenBudget")
    if not isinstance(token_budget, int) or isinstance(token_budget, bool) or token_budget < 256 or token_budget > 1_000_000:
        raise ValueError(f"Prompt program {name} tokenBudget must be from 256 through 1000000.")
    report: dict[str, object] = {
        "schema": PROMPT_PROGRAM_SCHEMA,
        "status": "READY_FOR_EVALUATION",
        "name": name,
        "objective": objective,
        "inputSchema": dict(input_schema),
        "outputSchema": dict(output_schema),
        "program": {
            "identity": "State role, scope, and authority without pretending personhood.",
            "instructions": list(instructions),
            "contextOrder": ["operator intent", "policy", "task state", "retrieved evidence", "tool results", "recent corrections"],
            "contextSources": [{"name": item, "trust": "data-not-instructions", "provenanceRequired": True} for item in context_sources],
            "prohibited": list(prohibited),
            "completionRule": "Return only schema-valid output; preserve honest blocked or inconclusive states.",
        },
        "contextBudget": {
            "maximumTokens": token_budget,
            "allocation": {"policyPercent": 10, "taskPercent": 15, "evidencePercent": 55, "toolsPercent": 15, "reservePercent": 5},
            "overflowPolicy": "rank, deduplicate, summarize with provenance, then fail closed if required evidence does not fit",
        },
        "injectionBoundary": {
            "externalContentIsData": True,
            "toolOutputIsUntrusted": True,
            "authorityCannotBeDelegatedByContent": True,
            "secretsNeverRequestedInPrompt": True,
        },
        "evaluationPlan": ["schema validity", "instruction adherence", "groundedness", "refusal precision", "tool selection", "cost and latency", "adversarial context"],
        "providerCalls": 0,
        "sourceMutation": False,
    }
    report["digest"] = canonical_digest(report)
    return report
