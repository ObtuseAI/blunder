from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from blunder.api.entrypoint import parse_session_token_line
from blunder.execution_budget import ExecutionBudget
from blunder.native_agent import (
    Proposal,
    ProposedChange,
    extract_json_object,
    parse_proposal,
    preserve_terminal_newlines,
    provider_json,
    run_validation,
    trusted_python_callers,
    validate_change_path,
)
from blunder.shell.server import MAX_JOB_OUTPUT, ShellRuntime, bounded_output, make_handler
from blunder.shell.intelligence import checkpoint_detail, restore_checkpoint
from blunder.shell.service import action_command, build_task_briefing, read_source_file, redact_text, validate_task
from blunder.semantic_editor import document_analysis, save_document, symbol_definitions
from blunder.verification_court import verification_court


REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = REPO_ROOT / "blunder" / "shell" / "static"


class BlunderShellBoundaryTests(unittest.TestCase):
    def test_session_token_parser_accepts_bom_framing_and_rejects_non_ascii(self) -> None:
        token = b"a" * 32
        self.assertEqual(parse_session_token_line(b"\xef\xbb\xbf" + token + b"\r\n"), token.decode("ascii"))
        with self.assertRaisesRegex(ValueError, "only ASCII"):
            parse_session_token_line((b"a" * 31) + b"\xff\n")

    def test_proposal_normalization_preserves_terminal_newline_structure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "docs" / "proof.md"
            target.parent.mkdir()
            target.write_text("before\n\n", encoding="utf-8")
            proposal = Proposal("edit", (ProposedChange("docs/proof.md", "after\n", "clarify"),), ())
            normalized = preserve_terminal_newlines(root, proposal)
        self.assertEqual(normalized.changes[0].content, "after\n\n")

    def test_provider_attempt_has_hard_total_deadline(self) -> None:
        blocked = threading.Event()
        budget = ExecutionBudget(300, 3, 100_000, 2.0, None)
        with mock.patch("blunder.native_agent.PROVIDER_ATTEMPT_TIMEOUT_SECONDS", 0.01), mock.patch(
            "blunder.native_agent.urllib.request.urlopen",
            side_effect=lambda request, timeout: blocked.wait(1),
        ), mock.patch("blunder.native_agent.time.sleep", return_value=None), self.assertRaisesRegex(
            RuntimeError, "hard 0.01-second deadline"
        ):
            provider_json("key", "model", "system", "user", 0.0, budget, "deadline-proof")

    def test_task_briefing_builds_context_without_provider_or_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "blunder").mkdir()
            (root / "blunder" / "engine.py").write_text("def plan_task():\n    return 'proof'\n", encoding="utf-8")
            briefing = build_task_briefing(root, "Improve plan task proof")
        self.assertEqual(briefing["contract"]["providerCallsBeforeApproval"], 0)
        self.assertTrue(briefing["contract"]["rollbackOnFailure"])
        self.assertGreaterEqual(briefing["context"]["sourceCount"], 1)

    def test_checkpoint_restore_is_hash_guarded_and_exactly_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "blunder" / "engine.py"
            target.parent.mkdir()
            target.write_text("after = True\n", encoding="utf-8")
            checkpoint_id = "20260710T210000000000Z"
            artifact = root / "artifacts" / "obtuse" / "blunder-native-agent" / checkpoint_id
            snapshot = artifact / "snapshot" / "blunder" / "engine.py"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text("before = True\n", encoding="utf-8")
            digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest().upper()
            report = {
                "status": "APPLIED_AND_VALIDATED",
                "task": "Improve engine",
                "proposal": {"summary": "Improve engine"},
                "checkpoint": {
                    "id": checkpoint_id,
                    "createdAt": "2026-07-10T21:00:00+00:00",
                    "restorable": True,
                    "files": [{
                        "path": "blunder/engine.py",
                        "existedBefore": True,
                        "beforeSha256": digest(snapshot),
                        "afterSha256": digest(target),
                    }],
                },
            }
            (artifact / "report.json").write_text(json.dumps(report), encoding="utf-8")
            self.assertTrue(checkpoint_detail(root, checkpoint_id)["diffs"][0]["changed"])
            with self.assertRaisesRegex(ValueError, "exact confirmation"):
                restore_checkpoint(root, checkpoint_id, "RESTORE")
            restored = restore_checkpoint(root, checkpoint_id, f"RESTORE {checkpoint_id}")
            self.assertEqual(restored["status"], "RESTORED")
            self.assertEqual(target.read_text(encoding="utf-8"), "before = True\n")

    def test_native_agent_accepts_bounded_source_and_rejects_private_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(validate_change_path(root, "blunder/core.py"), root / "blunder" / "core.py")
            with self.assertRaises(ValueError):
                validate_change_path(root, ".git/config")
            with self.assertRaises(ValueError):
                validate_change_path(root, "outside.txt")

    def test_native_agent_parses_strict_change_proposals(self) -> None:
        proposal = parse_proposal(
            extract_json_object(
                'result: {"summary":"Polish shell","changes":[{"path":"blunder/ui.py","content":"value = 1\\n","reason":"clarity"}],"validationNotes":["compile"]}'
            )
        )
        self.assertEqual(proposal.summary, "Polish shell")
        self.assertEqual(proposal.changes[0].path, "blunder/ui.py")
        with self.assertRaisesRegex(ValueError, "complete non-empty content"):
            parse_proposal(
                {
                    "summary": "Invalid empty replacement",
                    "changes": [{"path": "blunder/ui.py", "content": "", "reason": "clarity"}],
                    "validationNotes": [],
                }
            )

    def test_task_gate_requires_explicit_provider_approval(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicit approval"):
            action_command(REPO_ROOT, "run_task", {"task": "Review the parser"})
        command, timeout_seconds = action_command(
            REPO_ROOT,
            "run_task",
            {"task": "Review the parser", "providerApproved": True},
        )
        self.assertIn("Review the parser", command)
        self.assertEqual(timeout_seconds, 3600)

    def test_evolution_action_is_local_and_supplies_required_paths(self) -> None:
        command, timeout_seconds = action_command(REPO_ROOT, "evolve", {})
        self.assertIn("--ledger-root", command)
        self.assertIn("--plan-path", command)
        self.assertNotIn("--provider-approved", command)
        self.assertEqual(timeout_seconds, 90)

    def test_capability_readiness_tool_is_native_and_provider_free(self) -> None:
        command, timeout_seconds = action_command(REPO_ROOT, "readiness", {})
        self.assertIn("blunder-s-tier-capability-audit.ps1", " ".join(command))
        self.assertIn("Validate", command)
        self.assertNotIn("--provider-approved", command)
        self.assertEqual(timeout_seconds, 300)

    def test_task_gate_blocks_high_impact_requests(self) -> None:
        blocked = (
            "Launch the benchmark now",
            "git push the current branch",
            "print the API key",
            "publish publicly when complete",
            "delete recursively from the workspace",
        )
        for task in blocked:
            with self.subTest(task=task), self.assertRaises(ValueError):
                validate_task(task)

    def test_source_reader_blocks_private_and_escaping_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            (root / ".env").write_text("SECRET=value\n", encoding="utf-8")
            self.assertEqual(read_source_file(root, "source.py")["content"], "value = 1\n")
            with self.assertRaises(ValueError):
                read_source_file(root, ".env")
            with self.assertRaises(ValueError):
                read_source_file(root, "../outside.py")

    def test_redaction_covers_tokens_and_environment_assignments(self) -> None:
        value = "OPENAI_API_KEY=secret-value-that-must-not-leak Bearer abcdefghijklmnopqrstuvwxyz"
        redacted = redact_text(value)
        self.assertNotIn("secret-value", redacted)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", redacted)

    def test_job_output_is_bounded_and_marks_truncation(self) -> None:
        output, truncated = bounded_output("a" * MAX_JOB_OUTPUT, "latest-proof")
        self.assertTrue(truncated)
        self.assertLessEqual(len(output), MAX_JOB_OUTPUT)
        self.assertTrue(output.endswith("latest-proof"))
        self.assertIn("Earlier output truncated", output)

    def test_generated_validation_has_no_credentials_and_no_external_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            test_path = root / "tests" / "test_boundary.py"
            test_path.parent.mkdir(parents=True)
            test_path.write_text(
                """import os
import socket
import subprocess
import sys
import unittest
from pathlib import Path

class BoundaryProof(unittest.TestCase):
    def test_boundary(self):
        self.assertNotIn('OPENROUTER_API_KEY', os.environ)
        with self.assertRaises(PermissionError):
            socket.create_connection(('1.1.1.1', 443), timeout=0.1)
        with self.assertRaises(PermissionError):
            socket.getaddrinfo('example.com', 443)
        with self.assertRaises(PermissionError):
            subprocess.run([sys.executable, '-c', 'print(1)'], check=False)
        with self.assertRaises(PermissionError):
            Path.home().joinpath('.blunder-secret-proof').read_text(encoding='utf-8')
""",
                encoding="utf-8",
            )
            changes = (ProposedChange("tests/test_boundary.py", test_path.read_text(encoding="utf-8"), "proof"),)
            artifact = root / "artifacts" / "validation"
            artifact.mkdir(parents=True)
            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "must-not-reach-validation"}):
                result = run_validation(root, changes, artifact, trusted_python_callers(root, changes))
        self.assertTrue(result["testsPassed"], result["testOutput"])
        self.assertNotIn("must-not-reach-validation", result["testOutput"])

    def test_verification_court_rejects_unmeasured_claims_before_model_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            change = ProposedChange("docs/proof.md", "All 99 tests pass.\n", "publish proof")
            result = verification_court(root, (change,), ("docs/proof.md",), None, "preflight")
        self.assertEqual(result["verdict"], "REJECT")
        self.assertTrue(result["deterministic"])

    def test_verification_court_allows_preserved_historical_measurements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "docs" / "proof.md"
            target.parent.mkdir()
            target.write_text("65 tests passing.\n", encoding="utf-8")
            change = ProposedChange(
                "docs/proof.md",
                "65 tests passing.\nCache identity is content addressed.\n",
                "clarify cache",
            )
            result = verification_court(root, (change,), ("docs/proof.md",), None, "preflight")
        self.assertEqual(result["verdict"], "APPROVE")

    def test_verification_court_requires_exact_applied_content_and_executable_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "blunder" / "proof.py"
            path.parent.mkdir()
            path.write_text("value = 2\n", encoding="utf-8")
            change = ProposedChange("blunder/proof.py", "value = 2\n", "verified replacement")
            result = verification_court(root, (change,), ("blunder/proof.py",), {"testsPassed": True}, "postflight")
        self.assertEqual(result["verdict"], "APPROVE")

    def test_semantic_editor_analyzes_navigates_and_checkpoint_saves(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "blunder" / "editor.py"
            path.parent.mkdir()
            before = "def first_symbol() -> int:\n    return 1\n"
            after = "def first_symbol() -> int:\n    return 2\n"
            path.write_text(before, encoding="utf-8")
            analysis = document_analysis("blunder/editor.py", before)
            definitions = symbol_definitions(root, "first_symbol", 5)
            expected = hashlib.sha256(before.encode("utf-8")).hexdigest().upper()
            with self.assertRaisesRegex(ValueError, "exact confirmation"):
                save_document(root, path, after, expected, "SAVE")
            result = save_document(root, path, after, expected, f"SAVE blunder/editor.py {expected[:12]}")
        self.assertEqual(analysis["symbols"][0]["name"], "first_symbol")
        self.assertEqual(definitions[0]["line"], 1)
        self.assertEqual(result["status"], "SAVED_TO_REVIEW_TREE")
        self.assertFalse(result["committed"])


class BlunderShellHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.root = root
        shutil.copytree(REPO_ROOT / "blunder" / "capability_packs", root / "blunder" / "capability_packs")
        (root / "configs" / "obtuse").mkdir(parents=True)
        shutil.copyfile(
            REPO_ROOT / "configs" / "obtuse" / "blunder-ccos-runtime.json",
            root / "configs" / "obtuse" / "blunder-ccos-runtime.json",
        )
        runtime = ShellRuntime(root, STATIC_ROOT, "test-session-token")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(runtime))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temporary.cleanup()

    def request_json(self, path: str, authenticated: bool) -> dict[str, object]:
        headers = {"X-Blunder-Token": "test-session-token"} if authenticated else {}
        with urlopen(Request(self.base_url + path, headers=headers), timeout=3) as response:
            value = json.loads(response.read().decode("utf-8"))
        self.assertIsInstance(value, dict)
        return value

    def post_json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=body,
            method="POST",
            headers={"X-Blunder-Token": "test-session-token", "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=3) as response:
            value = json.loads(response.read().decode("utf-8"))
        self.assertIsInstance(value, dict)
        return value

    def test_health_is_minimal_and_unauthenticated(self) -> None:
        health = self.request_json("/api/health", authenticated=False)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["version"], 4)
        self.assertNotIn("root", health)

    def test_api_rejects_missing_session_token(self) -> None:
        with self.assertRaises(HTTPError) as context:
            self.request_json("/api/jobs", authenticated=False)
        self.assertEqual(context.exception.code, 401)

    def test_api_rejects_duplicate_keys_and_non_finite_numbers(self) -> None:
        for body in (b'{"kind":"memory","kind":"genome","payload":{}}', b'{"kind":"memory","payload":{"score":NaN}}'):
            request = Request(
                self.base_url + "/api/ccos/records",
                data=body,
                method="POST",
                headers={"X-Blunder-Token": "test-session-token", "Content-Type": "application/json"},
            )
            with self.subTest(body=body), self.assertRaises(HTTPError) as context:
                urlopen(request, timeout=3)
            self.assertEqual(context.exception.code, 400)

    def test_static_shell_has_security_headers(self) -> None:
        with urlopen(self.base_url + "/", timeout=3) as response:
            body = response.read().decode("utf-8")
            self.assertIn("<title>Blunder ", body)
            self.assertIn("Focus Shell</title>", body)
            self.assertIn('id="ccosRuntimeState"', body)
            self.assertIn('id="runCcosCanaryButton"', body)
            self.assertIn('id="ccosRecoveryConfirmation"', body)
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])

    def test_briefing_endpoint_is_authenticated_and_provider_free(self) -> None:
        payload = self.post_json("/api/briefing", {"task": "Inspect source proof"})
        self.assertEqual(payload["schema"], "obtuse.blunder.task-briefing.v1")
        self.assertEqual(payload["contract"]["providerCallsBeforeApproval"], 0)

    def test_ccos_endpoints_are_authenticated_durable_and_authority_closed(self) -> None:
        initial = self.request_json("/api/ccos", authenticated=True)
        record = self.post_json(
            "/api/ccos/records",
            {"kind": "lineage", "payload": {"genomeDigest": "A" * 64, "status": "OBSERVED"}},
        )
        canary = self.post_json(
            "/api/ccos/canary",
            {
                "activationConfirmation": "SIMULATE_CCOS_ACTIVATION_GATE",
                "rollbackConfirmation": "VERIFY_CCOS_ROLLBACK_GATE",
            },
        )
        records = self.request_json("/api/ccos/records?kind=lineage", authenticated=True)
        retention = self.request_json("/api/ccos/retention", authenticated=True)
        recovery = self.request_json("/api/ccos/recovery", authenticated=True)
        current = self.request_json("/api/ccos", authenticated=True)
        self.assertEqual(initial["status"], "OPERATIONAL_OBSERVATION_ONLY")
        self.assertFalse(record["authorityGranted"])
        self.assertEqual(canary["status"], "PASS_AUTHORITY_CLOSED_NO_CHAMPION")
        self.assertEqual(canary["providerCalls"], 0)
        self.assertEqual(records["count"], 2)
        self.assertEqual(records["total"], 2)
        self.assertEqual(retention["status"], "WITHIN_POLICY")
        self.assertEqual(recovery["status"], "CLEAN")
        self.assertEqual(current["generation"], 8)
        self.assertFalse(current["authority"]["automaticActivationAllowed"])
        with self.assertRaises(HTTPError) as context:
            self.post_json("/api/ccos/records", {"kind": "memory", "payload": {"authority": "grant"}})
        self.assertEqual(context.exception.code, 400)
        with self.assertRaises(HTTPError) as context:
            self.post_json("/api/ccos/recover", {"confirmation": "recover"})
        self.assertEqual(context.exception.code, 400)
        with self.assertRaises(HTTPError) as context:
            self.request_json("/api/ccos/records?limit=201", authenticated=True)
        self.assertEqual(context.exception.code, 400)

    def test_capability_endpoints_expose_and_route_first_party_fabric(self) -> None:
        registry = self.request_json("/api/capabilities", authenticated=True)
        route = self.post_json("/api/capabilities/route", {"task": "Research and debug runtime proof"})
        self.assertEqual(registry["counts"]["plugins"], 8)
        self.assertTrue(registry["firstPartyOnly"])
        self.assertGreaterEqual(len(route["skills"]), 2)
        self.assertFalse(route["externalEffectsAllowed"])

    def test_frontier_endpoints_are_provider_free_and_authority_closed(self) -> None:
        frontier = self.request_json("/api/frontier", authenticated=True)
        discovery = self.post_json(
            "/api/discovery/brief",
            {"question": "Which first-party capability contract should be tested next?"},
        )
        self_coding = self.post_json(
            "/api/self-coding/preflight",
            {"task": "Improve one typed function", "targetPath": "blunder/target.py"},
        )
        self.assertEqual(frontier["status"], "OPERATIONAL")
        self.assertEqual(frontier["providerCalls"], 0)
        self.assertEqual(discovery["providerCalls"], 0)
        self.assertEqual(self_coding["status"], "BLOCKED_AUTHORIZATION_FILE_MISSING")
        self.assertFalse(self_coding["integrationAuthorized"])
        self.assertFalse(self_coding["pushAuthorized"])

    def test_product_studio_endpoints_compile_blueprint_and_device_contracts(self) -> None:
        studio = self.request_json("/api/product", authenticated=True)
        blueprint = self.post_json(
            "/api/product/blueprint",
            {
                "productName": "Atlas",
                "goal": "Help teams create resilient private product launches.",
                "audience": ["operator"],
                "platforms": ["web"],
                "capabilities": ["create a launch"],
                "entities": [],
                "constraints": [],
            },
        )
        matrix = self.post_json("/api/product/device-matrix", {"locale": "en-US", "categories": []})
        self.assertEqual(studio["status"], "OPERATIONAL")
        self.assertEqual(studio["providerCalls"], 0)
        self.assertEqual(blueprint["status"], "READY_FOR_PRODUCT_PLANNING")
        self.assertEqual(matrix["profileCount"], 8)
        self.assertFalse(matrix["executionAuthority"])

    def test_quorum_kernel_endpoints_expose_graph_impact_and_gated_metabolism(self) -> None:
        kernel = self.request_json("/api/kernel", authenticated=True)
        impact = self.post_json(
            "/api/kernel/impact",
            {"changedPaths": ["blunder/capability_packs/kernel/manifest.json"], "maximumDepth": 3},
        )
        metabolism = self.post_json(
            "/api/kernel/metabolism",
            {
                "telemetry": {
                    "operatorIdle": False,
                    "foregroundMissionActive": False,
                    "cpuPercent": 10,
                    "availableMemoryBytes": 8589934592,
                    "thermalState": "nominal",
                    "queueFailures": 0,
                    "destructiveAuthority": False,
                },
                "budget": {
                    "maximumTasks": 3,
                    "maximumSeconds": 180,
                    "maximumCpuPercent": 35,
                    "minimumAvailableMemoryBytes": 2147483648,
                },
            },
        )
        self.assertEqual(kernel["status"], "OPERATIONAL")
        self.assertEqual(len(kernel["surfaces"]), 10)
        self.assertGreaterEqual(impact["counts"]["nodes"], 1)
        self.assertEqual(metabolism["status"], "PAUSED_RESOURCE_OR_SAFETY_GATE")
        self.assertEqual(metabolism["selectedTasks"], [])

    def test_creation_forge_endpoints_compile_open_first_party_design_and_automation(self) -> None:
        forge = self.request_json("/api/creation", authenticated=True)
        creative = self.post_json(
            "/api/creation/creative-brief",
            {
                "name": "Lumen",
                "outcome": "Make AI automation design understandable and reviewable.",
                "audiences": ["operator"],
                "moments": ["shape intent", "review effects"],
                "principles": ["clarity", "control"],
                "avoid": ["generic dashboard"],
                "directionCount": 3,
            },
        )
        automation = self.post_json(
            "/api/creation/automation",
            {
                "name": "Review flow",
                "outcome": "Draft and review without external effects.",
                "nodes": [
                    {"id": "start", "kind": "trigger", "label": "Start", "risk": "none", "timeoutSeconds": 10},
                    {"id": "review", "kind": "human", "label": "Review", "risk": "none", "timeoutSeconds": 100},
                    {"id": "done", "kind": "terminal", "label": "Done", "risk": "none", "timeoutSeconds": 10},
                ],
                "edges": [{"from": "start", "to": "review"}, {"from": "review", "to": "done"}],
            },
        )
        self.assertEqual(forge["status"], "OPERATIONAL")
        self.assertEqual(len(forge["surfaces"]), 10)
        self.assertEqual(forge["dependencies"], [])
        self.assertEqual(creative["status"], "READY_FOR_CREATIVE_REVIEW")
        self.assertEqual(automation["status"], "READY_FOR_DRY_RUN")
        self.assertFalse(automation["authority"]["execute"])

    def test_mission_fabric_intelligence_and_learning_endpoints_are_authenticated(self) -> None:
        intelligence = self.request_json("/api/repository-intelligence", authenticated=True)
        learning = self.request_json("/api/mission-learning", authenticated=True)
        branches = self.request_json("/api/mission-branches", authenticated=True)
        self.assertGreaterEqual(intelligence["counts"]["files"], 6)
        self.assertEqual(learning["routingPolicy"], "penalize-reverted-prefer-high-score")
        self.assertTrue(branches["isolation"]["integrationRequiresExactConfirmation"])


if __name__ == "__main__":
    unittest.main()
