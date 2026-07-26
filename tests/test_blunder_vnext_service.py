from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from blunder.api.contracts import TaskStageV1, TaskStateV1, load_project_policy, public_value, strict_json_loads
from blunder.api.events import TaskEventLedger
from blunder.api.service import VNextService
from blunder.engineering_graph import build_engineering_graph, projection_path, query_engineering_graph
from blunder.shell.server import ShellRuntime, make_handler
from tests.test_blunder_autonomous_promotion import passing_evidence


REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = REPO_ROOT / "blunder" / "shell" / "static"


def prepare_root(directory: str) -> Path:
    root = Path(directory)
    shutil.copytree(REPO_ROOT / "blunder" / "capability_packs", root / "blunder" / "capability_packs")
    (root / "configs" / "obtuse").mkdir(parents=True)
    for name in ("blunder-ccos-runtime.json", "blunder-vnext-project-policy.json"):
        shutil.copyfile(REPO_ROOT / "configs" / "obtuse" / name, root / "configs" / "obtuse" / name)
    (root / "blunder" / "engine.py").write_text("def inspect_project():\n    return 'ready'\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_engine.py").write_text("from blunder.engine import inspect_project\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "decision.md").write_text("# Product decision\n\nKeep authority closed.\n", encoding="utf-8")
    return root


def task_request(objective: str, success_criteria: list[str]) -> dict[str, object]:
    return {
        "objective": objective,
        "constraints": ["Keep external authority disabled"],
        "successCriteria": success_criteria,
        "deliverables": ["Reviewable evidence"],
        "assumptions": [],
        "unknowns": [],
    }


class VNextContractTests(unittest.TestCase):
    def test_strict_json_rejects_duplicate_and_non_finite_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate key"):
            strict_json_loads('{"objective":"one","objective":"two"}', "request")
        with self.assertRaisesRegex(ValueError, "non-finite"):
            strict_json_loads('{"cost":NaN}', "request")

    def test_default_project_policy_is_provider_and_deployment_closed(self) -> None:
        policy = load_project_policy(REPO_ROOT, REPO_ROOT / "configs" / "obtuse" / "blunder-vnext-project-policy.json")
        self.assertFalse(policy.provider.enabled)
        self.assertEqual(policy.provider.maximum_calls_per_task, 0)
        self.assertIsNone(policy.provider.credential_reference)
        self.assertFalse(policy.external_deployment_allowed)


class VNextEventLedgerTests(unittest.TestCase):
    def test_event_chain_is_durable_replayable_and_cursor_guarded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = TaskEventLedger(root)
            first = ledger.append(
                "task-001",
                "project-001",
                "task.created",
                TaskStageV1.UNDERSTANDING,
                TaskStateV1.UNDERSTANDING,
                (),
                (),
                (),
                {"providerCalls": 0},
                {"objective": "Inspect"},
                datetime.now(timezone.utc),
            )
            second = ledger.append(
                "task-001",
                "project-001",
                "task.cancelled",
                TaskStageV1.UNDERSTANDING,
                TaskStateV1.CANCELLED,
                (first.event_digest,),
                (),
                (),
                {"providerCalls": 0},
                {},
                datetime.now(timezone.utc),
            )
            replay = TaskEventLedger(root).events_after(0, "task-001", 20)
            self.assertEqual([item.sequence for item in replay], [1, 2])
            self.assertEqual(second.previous_event_digest, first.event_digest)
            self.assertEqual(ledger.recovery_report()["status"], "CLEAN")
            with self.assertRaisesRegex(ValueError, "stale or ahead"):
                ledger.events_after(3, None, 20)

    def test_event_tamper_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = TaskEventLedger(root)
            event = ledger.append(
                "task-001",
                "project-001",
                "task.created",
                TaskStageV1.UNDERSTANDING,
                TaskStateV1.UNDERSTANDING,
                (),
                (),
                (),
                {"providerCalls": 0},
                {},
                datetime.now(timezone.utc),
            )
            path = next((root / "artifacts" / "obtuse" / "blunder-vnext-v1" / "events" / "records").glob("*.json"))
            value = json.loads(path.read_text(encoding="utf-8"))
            value["state"] = "READY_FOR_REVIEW"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "digest does not match"):
                TaskEventLedger(root).events_after(0, None, 20)
            self.assertEqual(event.state, TaskStateV1.UNDERSTANDING)


class VNextServiceTests(unittest.TestCase):
    def test_task_creation_persists_supervision_and_provider_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = prepare_root(directory)
            service = VNextService(root, root / "configs" / "obtuse" / "blunder-vnext-project-policy.json")
            task = service.create_task(task_request("Implement a typed parser", ["The parser passes integration validation"]))
            self.assertEqual(task["state"], "NEEDS_APPROVAL")
            self.assertFalse(task["contract"]["authority"]["provider"])
            self.assertFalse(task["contract"]["authority"]["network"])
            self.assertGreaterEqual(len(task["selectedAgents"]), 4)
            approval = task["approvals"][0]
            decided = service.decide_approval(approval["approvalId"], {"decision": approval["exactApprovePhrase"]})
            self.assertEqual(decided["state"], "BLOCKED")
            self.assertIn("credential reference", decided["statusReason"])
            self.assertFalse(service.events.public_events_after(0, None, 20)["events"][1]["payload"]["authorityGranted"])
            reloaded = VNextService(root, root / "configs" / "obtuse" / "blunder-vnext-project-policy.json")
            self.assertEqual(reloaded.get_task(task["contract"]["taskId"])["state"], "BLOCKED")

    def test_material_ambiguity_blocks_before_provider_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = prepare_root(directory)
            service = VNextService(root, root / "configs" / "obtuse" / "blunder-vnext-project-policy.json")
            task = service.create_task(task_request("Fix the parser", []))
            self.assertEqual(task["state"], "BLOCKED")
            self.assertEqual(task["approvals"], [])
            self.assertIn("success criterion", task["statusReason"])

    def test_cancel_is_idempotent_and_integration_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = prepare_root(directory)
            service = VNextService(root, root / "configs" / "obtuse" / "blunder-vnext-project-policy.json")
            task = service.create_task(task_request("Explain the repository", ["Return cited source evidence"]))
            task_id = task["contract"]["taskId"]
            cancelled = service.cancel_task(task_id)
            self.assertEqual(cancelled["state"], "CANCELLED")
            self.assertEqual(service.cancel_task(task_id)["eventSequence"], cancelled["eventSequence"])
            with self.assertRaisesRegex(ValueError, "not backed by a verified isolated mission"):
                service.integrate_task(task_id, {"confirmation": f"INTEGRATE {task_id}"})


class EngineeringGraphTests(unittest.TestCase):
    def test_projection_is_rebuildable_and_queryable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = prepare_root(directory)
            summary = build_engineering_graph(root)
            self.assertEqual(summary["status"], "REBUILT_FROM_CANONICAL_SOURCE")
            self.assertGreater(summary["nodeCount"], 3)
            self.assertGreaterEqual(summary["edgeCount"], 1)
            self.assertTrue(projection_path(root).is_file())
            connection = sqlite3.connect(projection_path(root))
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM metadata").fetchone()[0], 3)
            finally:
                connection.close()
            query = query_engineering_graph(root, "engine", 20)
            self.assertTrue(any(item["sourcePath"] == "blunder/engine.py" for item in query["results"]))


class VNextHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = prepare_root(self.temporary.name)
        runtime = ShellRuntime(self.root, STATIC_ROOT, "vnext-test-token")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(runtime))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temporary.cleanup()

    def request_json(self, path: str) -> dict[str, object]:
        request = Request(self.base_url + path, headers={"X-Blunder-Token": "vnext-test-token"})
        with urlopen(request, timeout=5) as response:
            value = json.loads(response.read().decode("utf-8"))
        self.assertIsInstance(value, dict)
        return value

    def post_json(self, path: str, value: dict[str, object]) -> dict[str, object]:
        request = Request(
            self.base_url + path,
            data=json.dumps(value).encode("utf-8"),
            method="POST",
            headers={"X-Blunder-Token": "vnext-test-token", "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))
        self.assertIsInstance(result, dict)
        return result

    def test_authenticated_v1_task_and_sse_flow(self) -> None:
        health = self.request_json("/api/v1/health")
        projects = self.request_json("/api/v1/projects")
        task = self.post_json("/api/v1/tasks", task_request("Explain the project", ["Return source evidence"]))
        task_id = task["contract"]["taskId"]
        fetched = self.request_json(f"/api/v1/tasks/{task_id}")
        self.assertEqual(health["status"], "READY")
        self.assertEqual(projects["projects"][0]["id"], "blunder-private")
        self.assertEqual(fetched["contract"]["taskId"], task_id)
        request = Request(
            self.base_url + "/api/v1/events/stream?after=0",
            headers={"X-Blunder-Token": "vnext-test-token"},
        )
        with urlopen(request, timeout=5) as response:
            stream = response.read().decode("utf-8")
            self.assertEqual(response.headers["Content-Type"], "text/event-stream; charset=utf-8")
        self.assertIn("event: task-event", stream)
        self.assertIn(task_id, stream)

    def test_v1_rejects_missing_token_unknown_fields_and_ahead_cursor(self) -> None:
        with self.assertRaises(HTTPError) as context:
            urlopen(self.base_url + "/api/v1/projects", timeout=3)
        self.assertEqual(context.exception.code, 401)
        malformed_token = Request(
            self.base_url + "/api/v1/projects",
            headers={"X-Blunder-Token": "é" * 32},
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(malformed_token, timeout=3)
        self.assertEqual(context.exception.code, 401)
        invalid = task_request("Explain the project", ["Return source evidence"])
        invalid["mode"] = "coding"
        with self.assertRaises(HTTPError) as context:
            self.post_json("/api/v1/tasks", invalid)
        self.assertEqual(context.exception.code, 400)
        with self.assertRaises(HTTPError) as context:
            self.request_json("/api/v1/events/stream?after=999")
        self.assertEqual(context.exception.code, 400)

    def test_v1_promotion_evaluates_fixed_thresholds_and_persists_the_decision(self) -> None:
        result = self.post_json(
            "/api/v1/promotions/evaluate",
            {"scope": "organ", "evidence": public_value(passing_evidence("http-organ"))},  # type: ignore[dict-item]
        )
        self.assertEqual(result["decision"]["status"], "PROMOTED_AUTONOMOUS_THRESHOLD_PASS")
        self.assertTrue(result["decision"]["automaticPromotionAuthorized"])
        state = self.request_json("/api/v1/promotions")
        self.assertEqual(state["sequence"], 1)
        self.assertEqual(state["promotedCount"], 1)
        self.assertFalse(state["manualPromotionPathAvailable"])


if __name__ == "__main__":
    unittest.main()
