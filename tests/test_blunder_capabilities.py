from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from blunder.capabilities import (
    action_for_tool,
    dispatch_hooks,
    load_registry,
    registry_public,
    route_task,
    route_task_with_bias,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class BlunderCapabilityFoundryTests(unittest.TestCase):
    def test_first_party_registry_loads_typed_extension_fabric(self) -> None:
        registry = load_registry(REPO_ROOT)
        public = registry_public(registry)
        self.assertEqual(public["counts"], {"plugins": 8, "skills": 35, "tools": 53, "hooks": 7})
        self.assertTrue(public["firstPartyOnly"])
        self.assertFalse(public["externalPluginsAllowed"])
        self.assertFalse(public["arbitraryCommandsAllowed"])

    def test_task_router_selects_small_relevant_skill_and_tool_stack(self) -> None:
        registry = load_registry(REPO_ROOT)
        first = route_task(registry, "Debug a stuck autonomous runtime timeout with proof", 5)
        second = route_task(registry, "Debug a stuck autonomous runtime timeout with proof", 5)
        self.assertEqual(first, second)
        selected = {str(skill["skill_id"]) for skill in first["skills"]}
        self.assertIn("engineering:root-cause-debug", selected)
        self.assertIn("autonomy:runtime-triage", selected)
        self.assertFalse(first["externalEffectsAllowed"])
        self.assertTrue(first["providerRequired"])
        self.assertTrue(first["sourceMutationPossible"])

    def test_task_router_does_not_pad_a_dominant_skill_with_weak_matches(self) -> None:
        registry = load_registry(REPO_ROOT)
        route = route_task(registry, "Prepare capability readiness for a private release", 5)
        selected = [str(skill["skill_id"]) for skill in route["skills"]]
        self.assertEqual(selected, ["quality:release-readiness"])

    def test_tool_invocation_is_closed_to_manifest_allowlisted_actions(self) -> None:
        registry = load_registry(REPO_ROOT)
        self.assertEqual(action_for_tool(registry, "quality:readiness-gate"), "readiness")
        with self.assertRaisesRegex(ValueError, "IDE-native context tool"):
            action_for_tool(registry, "engineering:context-forge")

    def test_outcome_bias_changes_future_skill_priority_without_expanding_permissions(self) -> None:
        registry = load_registry(REPO_ROOT)
        route = route_task_with_bias(
            registry,
            "Improve and simplify an interface feature",
            5,
            {"product:radical-simplification": 25.0, "engineering:build-with-proof": -10.0},
        )
        self.assertEqual(route["skills"][0]["skill_id"], "product:radical-simplification")
        self.assertTrue(route["outcomeLearningApplied"])
        self.assertFalse(route["externalEffectsAllowed"])

    def test_lifecycle_hooks_emit_bounded_audit_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "blunder" / "capability_packs"
            shutil.copytree(REPO_ROOT / "blunder" / "capability_packs", target)
            registry = load_registry(root)
            hooks = dispatch_hooks(root, registry, "task.briefed", {"task": "Research proof", "sourceCount": 4})
            ledger = root / "artifacts" / "obtuse" / "blunder-capability-foundry-v1" / "hook-events.jsonl"
            record = json.loads(ledger.read_text(encoding="utf-8").strip())
        self.assertEqual(
            hooks,
            ("architecture:architecture-brief-journal", "research:briefing-provenance-journal"),
        )
        self.assertEqual(record["event"], "task.briefed")
        self.assertEqual(record["payload"]["sourceCount"], 4)

    def test_registry_rejects_non_first_party_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "blunder" / "capability_packs"
            shutil.copytree(REPO_ROOT / "blunder" / "capability_packs", target)
            path = target / "research" / "manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["firstParty"] = False
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "enabled and first-party"):
                load_registry(root)


if __name__ == "__main__":
    unittest.main()
