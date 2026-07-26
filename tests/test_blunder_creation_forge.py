from __future__ import annotations

import unittest

from blunder.ai_architect import compile_ai_architecture
from blunder.automation_fabric import compile_automation
from blunder.creative_direction import compile_creative_brief
from blunder.open_tool_foundry import compile_tool_contract
from blunder.prompt_program import compile_prompt_program
from blunder.trace_creativity_lab import archive_creative_candidates, evaluate_trace


class BlunderCreationForgeIntegrationTests(unittest.TestCase):
    def test_creative_brief_preserves_distinct_directions_and_complete_experience_states(self) -> None:
        report = compile_creative_brief(
            {
                "name": "Lumen",
                "outcome": "Help an operator design and safely launch a useful AI automation.",
                "audiences": ["operator", "reviewer"],
                "moments": ["shape the intent", "review the automation", "recover a failed step"],
                "principles": ["clarity before density", "evidence before confidence", "delight through understanding"],
                "avoid": ["generic dashboard", "decorative AI glow", "hidden automation"],
                "directionCount": 3,
            }
        )
        self.assertEqual(report["status"], "READY_FOR_CREATIVE_REVIEW")
        self.assertEqual(len(report["directions"]), 3)
        self.assertEqual(len({item["id"] for item in report["directions"]}), 3)
        self.assertIn("uncertain", report["storyboards"][0]["requiredStates"])
        self.assertEqual(report["providerCalls"], 0)

    def test_ai_architecture_keeps_decisions_human_owned_and_builds_evaluation_contract(self) -> None:
        report = compile_ai_architecture(
            {
                "name": "Lumen planner",
                "outcome": "Turn an automation idea into a reviewable implementation plan.",
                "userDecisions": ["approve external effects", "select the final direction"],
                "inputs": ["operator brief", "repository evidence"],
                "outputs": ["typed plan", "risk register"],
                "tools": ["source search", "validation runner"],
                "risks": ["prompt injection", "false completion", "duplicate external effect"],
            }
        )
        self.assertTrue(all(item["owner"] == "user" for item in report["decisionContract"]))
        self.assertIn("trace", report["evaluationContract"])
        self.assertIn("manual control", " ".join(report["experienceContract"]["control"]))

    def test_automation_compiler_requires_human_gate_compensation_and_idempotency(self) -> None:
        report = compile_automation(
            {
                "name": "Release briefing",
                "outcome": "Prepare and approve a release briefing without silently publishing it.",
                "nodes": [
                    {"id": "start", "kind": "trigger", "label": "Request received", "risk": "none", "timeoutSeconds": 30},
                    {"id": "draft", "kind": "ai", "label": "Draft briefing", "risk": "low", "timeoutSeconds": 90, "idempotencyKey": "release:draft"},
                    {"id": "approve", "kind": "human", "label": "Approve publication", "risk": "none", "timeoutSeconds": 86400},
                    {"id": "publish", "kind": "action", "label": "Publish briefing", "risk": "high", "timeoutSeconds": 60, "idempotencyKey": "release:publish", "compensation": "unpublish the exact revision"},
                    {"id": "done", "kind": "terminal", "label": "Complete", "risk": "none", "timeoutSeconds": 30},
                ],
                "edges": [
                    {"from": "start", "to": "draft"},
                    {"from": "draft", "to": "approve"},
                    {"from": "approve", "to": "publish", "condition": "approved"},
                    {"from": "publish", "to": "done"},
                ],
            }
        )
        self.assertEqual(report["status"], "READY_FOR_DRY_RUN")
        self.assertFalse(report["authority"]["execute"])
        with self.assertRaisesRegex(ValueError, "idempotencyKey"):
            compile_automation(
                {
                    "name": "Broken",
                    "outcome": "Prove unsafe graph rejection.",
                    "nodes": [
                        {"id": "start", "kind": "trigger", "label": "Start", "risk": "none", "timeoutSeconds": 1},
                        {"id": "write", "kind": "action", "label": "Write", "risk": "low", "timeoutSeconds": 1},
                        {"id": "done", "kind": "terminal", "label": "Done", "risk": "none", "timeoutSeconds": 1},
                    ],
                    "edges": [{"from": "start", "to": "write"}, {"from": "write", "to": "done"}],
                }
            )
        with self.assertRaisesRegex(ValueError, "unreachable-from-trigger"):
            compile_automation(
                {
                    "name": "Disconnected",
                    "outcome": "Reject graph islands before runtime.",
                    "nodes": [
                        {"id": "start", "kind": "trigger", "label": "Start", "risk": "none", "timeoutSeconds": 1},
                        {"id": "island", "kind": "transform", "label": "Island", "risk": "none", "timeoutSeconds": 1},
                        {"id": "done", "kind": "terminal", "label": "Done", "risk": "none", "timeoutSeconds": 1},
                    ],
                    "edges": [{"from": "start", "to": "done"}],
                }
            )

    def test_open_tool_and_prompt_program_compile_to_open_schema_bound_contracts(self) -> None:
        tool = compile_tool_contract(
            {
                "name": "inspect_trace",
                "description": "Read one content-addressed trace without changing runtime state.",
                "permissions": ["artifact:read", "runtime:read"],
                "risk": "read-only",
                "approval": "never",
                "idempotency": "content digest",
                "timeoutSeconds": 30,
                "inputSchema": {"type": "object", "properties": {"traceId": {"type": "string"}}, "required": ["traceId"]},
                "outputSchema": {"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]},
            }
        )
        prompt = compile_prompt_program(
            {
                "name": "Trace analyst",
                "objective": "Explain the first failed decision using trace evidence.",
                "instructions": ["Separate observations from inference", "Name missing evidence"],
                "contextSources": ["content-addressed trace", "evaluation policy"],
                "prohibited": ["invented tool results", "silent authority expansion"],
                "tokenBudget": 4096,
                "inputSchema": {"type": "object", "properties": {"traceId": {"type": "string"}}},
                "outputSchema": {"type": "object", "properties": {"finding": {"type": "string"}}},
            }
        )
        self.assertEqual(tool["protocolViews"]["mcp"]["name"], "inspect_trace")
        self.assertTrue(prompt["injectionBoundary"]["toolOutputIsUntrusted"])

    def test_trace_grading_and_quality_diversity_archive_require_evidence(self) -> None:
        trace = evaluate_trace(
            {
                "traceId": "trace-1",
                "spans": [
                    {"id": "input", "kind": "input", "name": "Task", "status": "completed", "durationMs": 1},
                    {"id": "tool", "parentId": "input", "kind": "tool", "name": "Search", "status": "completed", "durationMs": 8, "toolApproved": True},
                    {"id": "output", "parentId": "input", "kind": "output", "name": "Answer", "status": "completed", "durationMs": 2, "grounded": True},
                ],
            }
        )
        archive = archive_creative_candidates(
            (
                {"id": "precision", "novelty": 0.35, "usefulness": 0.94, "coherence": 0.95, "accessibility": 0.96, "evidence": "prototype-a"},
                {"id": "signal", "novelty": 0.88, "usefulness": 0.91, "coherence": 0.84, "accessibility": 0.9, "evidence": "prototype-b"},
                {"id": "kinetic", "novelty": 0.86, "usefulness": 0.72, "coherence": 0.55, "accessibility": 0.82, "evidence": "prototype-c"},
            )
        )
        self.assertEqual(trace["status"], "PASS")
        self.assertEqual(len(archive["archive"]), 3)
        self.assertIsNotNone(archive["champion"])
        cyclic_trace = evaluate_trace(
            {
                "traceId": "trace-cycle",
                "spans": [
                    {"id": "root", "kind": "input", "name": "Task", "status": "completed", "durationMs": 1},
                    {"id": "a", "parentId": "b", "kind": "model", "name": "A", "status": "completed", "durationMs": 1},
                    {"id": "b", "parentId": "a", "kind": "model", "name": "B", "status": "completed", "durationMs": 1},
                    {"id": "output", "parentId": "root", "kind": "output", "name": "Answer", "status": "completed", "durationMs": 1, "grounded": True},
                ],
            }
        )
        self.assertEqual(cyclic_trace["status"], "FAIL")
        self.assertFalse(cyclic_trace["gates"]["structure"])


if __name__ == "__main__":
    unittest.main()
