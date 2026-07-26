from __future__ import annotations

import json
import mimetypes
import os
import secrets
import subprocess
import threading
import time
import uuid
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import parse_qs, urlparse

from blunder.api.service import VNextService
from blunder.capabilities import (
    CapabilityRegistry,
    action_for_tool,
    dispatch_hooks,
    load_registry,
    registry_public,
    route_task,
)
from blunder.autonomy_lane import SelfCodingBudget, SelfCodingRequest, read_authorization, self_coding_preflight
from blunder.app_blueprint import compile_blueprint
from blunder.ai_architect import compile_ai_architecture
from blunder.asset_intelligence import asset_intelligence_report
from blunder.automation_fabric import compile_automation
from blunder.creative_direction import compile_creative_brief
from blunder.ccos_runtime import (
    CCOSRuntimeConfig,
    append_runtime_record,
    inspect_runtime_recovery,
    load_runtime_config,
    recover_last_known_good,
    retention_report,
    runtime_records,
    runtime_state,
)
from blunder.ccos_canary import run_operational_canary
from blunder.constitutional_adapters import (
    record_process_boundary,
    record_requested_denial_boundaries,
    record_shell_action_boundary,
)
from blunder.design_system import compile_design_system
from blunder.deterministic_shards import execute_shard_suite, parse_shard_spec
from blunder.device_matrix import build_device_matrix
from blunder.discovery_engine import build_discovery_brief
from blunder.engineering_graph import query_engineering_graph
from blunder.game_studio import game_studio_report
from blunder.language_intelligence import completions, references, rename_plan, structural_diff, workspace_diagnostics
from blunder.mobile_foundry import mobile_project_report
from blunder.open_tool_foundry import compile_tool_contract
from blunder.mission_campaign import campaign_summary
from blunder.polyglot_intelligence import polyglot_index, polyglot_references
from blunder.product_arena import load_product_suite, parse_measurement as parse_product_measurement, product_arena_report
from blunder.prompt_program import compile_prompt_program
from blunder.quorum_graph import build_quorum_graph, impact_frontier
from blunder.quorum_kernel import (
    kernel_snapshot,
    metabolic_plan,
    parse_metabolic_budget,
    parse_vote,
    quorum_decision,
    timeline_report,
)
from blunder.visual_judgment import visual_judgment_report
from blunder.web_lab import web_production_report
from blunder.trace_creativity_lab import archive_creative_candidates, evaluate_trace
from blunder.missions import MissionBranchManager, MissionFabricManager, outcome_summary
from blunder.repository_intelligence import build_repository_index, intelligence_public, query_repository
from blunder.shell.service import (
    action_command,
    analyze_source_file,
    build_task_briefing,
    definition_locations,
    read_source_file,
    redact_text,
    sanitized_environment,
    search_files,
    search_source_content,
    save_source_file,
    shell_artifact_root,
    utc_now,
    validate_task,
    workspace_state,
)
from blunder.shell.intelligence import (
    append_job_history,
    checkpoint_detail,
    mission_control_state,
    restore_checkpoint,
)


MAX_JOB_OUTPUT = 40_000
MAX_REQUEST_BYTES = 16_384
TRUNCATION_NOTICE = "[Earlier output truncated; showing the latest local proof.]\n"


def bounded_output(existing: str, incoming: str) -> tuple[str, bool]:
    combined = existing + incoming
    if len(combined) <= MAX_JOB_OUTPUT:
        return combined, False
    available = MAX_JOB_OUTPUT - len(TRUNCATION_NOTICE)
    return TRUNCATION_NOTICE + combined[-available:], True


@dataclass
class JobRecord:
    job_id: str
    action_id: str
    label: str
    status: str
    created_at: str
    intent: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    output: str = ""
    output_truncated: bool = False
    error: str | None = None
    process: subprocess.Popen[str] | None = field(default=None, repr=False)

    def public(self) -> dict[str, object]:
        return {
            "id": self.job_id,
            "action": self.action_id,
            "label": self.label,
            "status": self.status,
            "createdAt": self.created_at,
            "intent": self.intent,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "exitCode": self.exit_code,
            "output": self.output,
            "outputTruncated": self.output_truncated,
            "error": self.error,
        }


class JobManager:
    def __init__(self, root: Path, on_complete: Callable[[Mapping[str, object]], None]) -> None:
        self._root = root
        self._on_complete = on_complete
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.RLock()

    def list_jobs(self) -> list[dict[str, object]]:
        with self._lock:
            values = sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)
            return [job.public() for job in values[:30]]

    def has_active_job(self) -> bool:
        with self._lock:
            return any(job.status in {"queued", "running"} for job in self._jobs.values())

    def get_job(self, job_id: str) -> dict[str, object] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.public() if job is not None else None

    def start(self, action_id: str, payload: Mapping[str, object]) -> dict[str, object]:
        record_requested_denial_boundaries(
            self._root,
            "authenticated-shell",
            str(payload.get("task") or ""),
            datetime.now(timezone.utc),
        )
        command, timeout_seconds = action_command(self._root, action_id, payload)
        record_shell_action_boundary(
            self._root,
            action_id,
            command,
            str(payload.get("task") or ""),
            datetime.now(timezone.utc),
        )
        with self._lock:
            active = [job for job in self._jobs.values() if job.status in {"queued", "running"}]
            if active:
                raise ValueError(f"Finish or stop {active[0].label} before starting another action.")
            job_id = uuid.uuid4().hex[:12]
            labels = {
                "status": "Workspace refresh",
                "evolve": "Evolution refresh",
                "planning_gate": "Planning gate",
                "tests": "Safety tests",
                "readiness": "Capability readiness",
                "run_task": "Blunder task",
            }
            job = JobRecord(
                job_id=job_id,
                action_id=action_id,
                label=labels[action_id],
                status="queued",
                created_at=utc_now(),
                intent=str(payload.get("task") or "").strip() or None,
            )
            self._jobs[job_id] = job
        thread = threading.Thread(
            target=self._run,
            args=(job_id, command, timeout_seconds),
            name=f"blunder-shell-job-{job_id}",
            daemon=True,
        )
        thread.start()
        return job.public()

    def _run(self, job_id: str, command: list[str], timeout_seconds: int) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = utc_now()
        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | int(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        timer: threading.Timer | None = None
        try:
            process = subprocess.Popen(
                command,
                cwd=self._root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=sanitized_environment(),
                creationflags=flags,
                bufsize=1,
            )
            with self._lock:
                job.process = process
            timer = threading.Timer(timeout_seconds, lambda: self.cancel(job_id, reason="Action timed out."))
            timer.daemon = True
            timer.start()
            assert process.stdout is not None
            for line in process.stdout:
                with self._lock:
                    job.output, truncated = bounded_output(job.output, redact_text(line))
                    job.output_truncated = job.output_truncated or truncated
            code = process.wait()
            with self._lock:
                if job.status != "cancelled":
                    job.status = "completed" if code == 0 else "failed"
                    job.exit_code = code
        except OSError as error:
            with self._lock:
                job.status = "failed"
                job.error = str(error)
        finally:
            if timer is not None:
                timer.cancel()
            with self._lock:
                job.finished_at = utc_now()
                job.process = None
                history = job.public()
            append_job_history(self._root, history)
            self._on_complete(history)

    def cancel(self, job_id: str, reason: str = "Stopped by operator.") -> dict[str, object]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError("Job does not exist.")
            process = job.process
            if job.status not in {"queued", "running"}:
                return job.public()
            job.status = "cancelled"
            job.error = reason
            job.finished_at = utc_now()
        if process is not None and process.poll() is None:
            if os.name == "nt":
                command = ["taskkill", "/PID", str(process.pid), "/T", "/F"]
                record_process_boundary(
                    self._root,
                    "authenticated-shell",
                    "shell-job-cancellation",
                    command,
                    None,
                    datetime.now(timezone.utc),
                )
                subprocess.run(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
                )
            else:
                process.terminate()
        return job.public()


class ShellRuntime:
    def __init__(self, root: Path, static_root: Path, token: str) -> None:
        self.root = root.resolve()
        self.static_root = static_root.resolve()
        self.token = token
        self._cache: dict[str, object] | None = None
        self._cache_time = 0.0
        self._cache_lock = threading.RLock()
        self.capabilities = load_registry(self.root)
        self.jobs = JobManager(self.root, self.complete_job)
        self.mission_branches = MissionBranchManager(self.root, self.capabilities, sys.executable)
        self.mission_fabric = MissionFabricManager(self.root, self.capabilities, self.mission_branches)
        vnext_policy = self.root / "configs" / "obtuse" / "blunder-vnext-project-policy.json"
        self.vnext = VNextService(self.root, vnext_policy) if vnext_policy.is_file() else None
        if self.vnext is not None and self.mission_branches.state()["available"] is True:
            self.vnext.bind_mission_manager(self.mission_branches)

    def require_vnext(self) -> VNextService:
        if self.vnext is None:
            raise ValueError("Blunder vNext is not configured because the standing project policy is missing.")
        return self.vnext

    def ccos_config(self) -> CCOSRuntimeConfig:
        return load_runtime_config(
            self.root,
            self.root / "configs" / "obtuse" / "blunder-ccos-runtime.json",
        )

    def ccos_state(self) -> dict[str, object]:
        return runtime_state(self.ccos_config())

    def ccos_records(self, kind: str, offset: int, limit: int) -> dict[str, object]:
        if offset < 0:
            raise ValueError("CCOS record offset must be non-negative.")
        if limit < 1 or limit > 200:
            raise ValueError("CCOS record limit must be between 1 and 200.")
        kinds = () if not kind else (kind,)
        records = runtime_records(self.ccos_config(), kinds)
        page = records[offset : offset + limit]
        return {
            "schema": "obtuse.blunder.ccos-runtime-record-list.v1",
            "kind": kind or "all",
            "records": list(page),
            "count": len(page),
            "total": len(records),
            "offset": offset,
            "limit": limit,
            "authorityGranted": False,
            "providerCalls": 0,
        }

    def ccos_append(self, body: Mapping[str, object]) -> dict[str, object]:
        payload = body.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("CCOS evidence append requires a payload object.")
        return append_runtime_record(
            self.ccos_config(),
            str(body.get("kind") or ""),
            payload,
            datetime.now(timezone.utc),
        )

    def ccos_canary(self, body: Mapping[str, object]) -> dict[str, object]:
        return run_operational_canary(
            self.ccos_config(),
            datetime.now(timezone.utc),
            str(body.get("activationConfirmation") or ""),
            str(body.get("rollbackConfirmation") or ""),
        )

    def ccos_recover(self, confirmation: str) -> dict[str, object]:
        return recover_last_known_good(self.ccos_config(), confirmation)

    def ccos_retention(self) -> dict[str, object]:
        return retention_report(self.ccos_config(), datetime.now(timezone.utc))

    def ccos_recovery_inspection(self) -> dict[str, object]:
        return inspect_runtime_recovery(self.ccos_config(), datetime.now(timezone.utc))

    def complete_job(self, job: Mapping[str, object]) -> None:
        self.invalidate_state()
        dispatch_hooks(self.root, self.capabilities, "mission.completed", job)

    def invalidate_state(self) -> None:
        with self._cache_lock:
            self._cache = None
            self._cache_time = 0.0

    def get_state(self, force: bool) -> dict[str, object]:
        with self._cache_lock:
            fresh = self._cache is not None and time.monotonic() - self._cache_time < 12
            if fresh and not force:
                return dict(self._cache)
        state = workspace_state(self.root)
        with self._cache_lock:
            self._cache = state
            self._cache_time = time.monotonic()
        return dict(state)

    def get_missions(self) -> dict[str, object]:
        state = self.get_state(False)
        runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
        return mission_control_state(self.root, self.jobs.list_jobs(), runtime)

    def restore(self, checkpoint_id: str, confirmation: str) -> dict[str, object]:
        if self.jobs.has_active_job():
            raise ValueError("Finish or stop the active foreground mission before restoring a checkpoint.")
        result = restore_checkpoint(self.root, checkpoint_id, confirmation)
        dispatch_hooks(self.root, self.capabilities, "checkpoint.restored", result)
        self.invalidate_state()
        return result

    def get_capabilities(self) -> dict[str, object]:
        return registry_public(self.capabilities)

    def route_capabilities(self, task: str) -> dict[str, object]:
        return route_task(self.capabilities, validate_task(task), 5)

    def invoke_capability_tool(self, tool_id: str) -> dict[str, object]:
        action_id = action_for_tool(self.capabilities, tool_id)
        return self.jobs.start(action_id, {})

    def frontier_state(self) -> dict[str, object]:
        raw_programs = self.mission_fabric.list().get("programs")
        programs = [item for item in raw_programs if isinstance(item, dict)] if isinstance(raw_programs, list) else []
        authorization_path = self.root / "configs" / "obtuse" / "blunder-operator-autonomy-authorization.json"
        return {
            "schema": "obtuse.blunder.frontier-workbench.v1",
            "status": "OPERATIONAL",
            "providerCalls": 0,
            "surfaces": {
                "intentFirewall": "ENFORCED_BEFORE_VERIFICATION",
                "evaluationArena": "DETERMINISTIC_AND_PROVENANCE_REQUIRED",
                "missionCampaigns": campaign_summary(programs),
                "selfCoding": "PREFLIGHT_AVAILABLE" if authorization_path.is_file() else "BLOCKED_AUTHORIZATION_FILE_MISSING",
                "languageIntelligence": "REFERENCES_COMPLETIONS_RENAME_DIAGNOSTICS_STRUCTURAL_DIFF",
                "discovery": "LOCAL_EVIDENCE_AND_FALSIFIABLE_HYPOTHESES",
                "reliability": "CAMPAIGN_RUNNER_AND_FAULT_DRILLS",
                "releaseGovernance": "SIGNED_MANIFEST_AND_EXACT_MERGE_CONFIRMATION",
            },
        }

    def self_coding_preflight(self, body: Mapping[str, object]) -> dict[str, object]:
        authorization_path = self.root / "configs" / "obtuse" / "blunder-operator-autonomy-authorization.json"
        if not authorization_path.is_file():
            return {
                "schema": "obtuse.blunder.bounded-self-coding-preflight.v1",
                "status": "BLOCKED_AUTHORIZATION_FILE_MISSING",
                "authorizationPath": authorization_path.relative_to(self.root).as_posix(),
                "integrationAuthorized": False,
                "commitAuthorized": False,
                "pushAuthorized": False,
            }
        target = str(body.get("targetPath") or "")
        request = SelfCodingRequest(
            task=validate_task(str(body.get("task") or "")),
            target_paths=(target,),
            budget=SelfCodingBudget(seconds=900, provider_calls=3, tokens=60_000, cost_usd=0.25),
        )
        return self_coding_preflight(self.root, read_authorization(authorization_path), request, True)

    def product_state(self) -> dict[str, object]:
        languages = polyglot_index(self.root)
        return {
            "schema": "obtuse.blunder.product-studio.v1",
            "status": "OPERATIONAL",
            "providerCalls": 0,
            "surfaces": {
                "appBlueprint": "READY",
                "visualJudgment": "READY_FOR_PNG_EVIDENCE",
                "deviceMatrix": "8_FIRST_PARTY_PROFILES",
                "mobile": mobile_project_report(self.root)["status"],
                "web": web_production_report(self.root)["status"],
                "game": game_studio_report(self.root)["status"],
                "designSystem": "READY",
                "assetIntelligence": "READY_FOR_PROVENANCE",
                "polyglot": {
                    "status": languages["status"],
                    "files": languages["fileCount"],
                    "languages": languages["languageCounts"],
                },
                "productArena": "PROVENANCE_REQUIRED",
            },
            "authority": {"build": False, "deploy": False, "storeSubmit": False, "publish": False},
        }

    def product_project_audit(self, domain: str) -> dict[str, object]:
        operations = {
            "mobile": mobile_project_report,
            "web": web_production_report,
            "game": game_studio_report,
        }
        operation = operations.get(domain.lower())
        if operation is None:
            raise ValueError("Product project audit domain must be mobile, web, or game.")
        return operation(self.root)

    def creation_state(self) -> dict[str, object]:
        return {
            "schema": "obtuse.blunder.creation-forge.v1",
            "status": "OPERATIONAL",
            "providerCalls": 0,
            "surfaces": {
                "creativeCompass": "DIVERSE_DIRECTIONS_READY",
                "experienceStoryboard": "EIGHT_STATE_JOURNEYS_READY",
                "generativeInterface": "OPEN_DESIGN_CONTRACT_READY",
                "aiSystemArchitect": "HUMAN_CONTROLLED_TOPOLOGY_READY",
                "automationCompiler": "DURABLE_DAG_AND_COMPENSATION_READY",
                "openToolFoundry": "MCP_COMPATIBLE_CONTRACT_READY",
                "promptPrograms": "SCHEMA_AND_INJECTION_BOUNDARY_READY",
                "traceObservatory": "STRUCTURAL_GRADING_READY",
                "creativeArena": "QUALITY_DIVERSITY_ARCHIVE_READY",
                "controlPlane": "API_CLI_IDE_CAPABILITY_PACK_READY",
            },
            "openStandards": ["JSON Schema", "MCP", "OpenTelemetry trace fields", "DTCG design tokens", "SVG", "Web standards"],
            "dependencies": [],
            "authority": {"execute": False, "externalEffects": False, "publish": False, "provider": False},
        }

    def kernel_graph(self) -> dict[str, object]:
        return build_quorum_graph(self.root, True)

    def kernel_impact(self, body: Mapping[str, object]) -> dict[str, object]:
        raw_paths = body.get("changedPaths")
        paths = [str(item) for item in raw_paths] if isinstance(raw_paths, list) else []
        raw_depth = body.get("maximumDepth")
        if not isinstance(raw_depth, int) or isinstance(raw_depth, bool):
            raise ValueError("Kernel impact maximumDepth must be an integer.")
        return impact_frontier(self.kernel_graph(), paths, raw_depth)

    def kernel_shards(self, body: Mapping[str, object]) -> dict[str, object]:
        raw_specs = body.get("specs")
        if not isinstance(raw_specs, list) or any(not isinstance(item, dict) for item in raw_specs):
            raise ValueError("Kernel shards require a specs object list.")
        graph = self.kernel_graph()
        specs = tuple(parse_shard_spec(item) for item in raw_specs if isinstance(item, dict))
        maximum_parallel = body.get("maximumParallel")
        if not isinstance(maximum_parallel, int) or isinstance(maximum_parallel, bool):
            raise ValueError("Kernel shards maximumParallel must be an integer.")
        return execute_shard_suite(self.root, specs, str(graph.get("fingerprint") or ""), maximum_parallel)

    def kernel_quorum(self, body: Mapping[str, object]) -> dict[str, object]:
        candidate = body.get("candidate")
        raw_votes = body.get("votes")
        raw_reports = body.get("shardReports")
        if not isinstance(candidate, dict):
            raise ValueError("Kernel quorum requires a candidate object.")
        if not isinstance(raw_votes, list) or any(not isinstance(item, dict) for item in raw_votes):
            raise ValueError("Kernel quorum requires a votes object list.")
        if not isinstance(raw_reports, list) or any(not isinstance(item, dict) for item in raw_reports):
            raise ValueError("Kernel quorum requires a shardReports object list.")
        graph = self.kernel_graph()
        votes = tuple(parse_vote(item) for item in raw_votes if isinstance(item, dict))
        reports = [dict(item) for item in raw_reports if isinstance(item, dict)]
        return quorum_decision(self.root, graph, candidate, votes, reports)

    def kernel_timeline(self, body: Mapping[str, object]) -> dict[str, object]:
        candidate = body.get("candidate")
        raw_votes = body.get("votes")
        raw_reports = body.get("shardReports")
        if not isinstance(candidate, dict) or not isinstance(raw_votes, list) or not isinstance(raw_reports, list):
            raise ValueError("Kernel timeline requires candidate, votes, and shardReports evidence.")
        if any(not isinstance(item, dict) for item in raw_votes) or any(not isinstance(item, dict) for item in raw_reports):
            raise ValueError("Kernel timeline votes and shardReports must contain only objects.")
        graph = self.kernel_graph()
        votes = tuple(parse_vote(item) for item in raw_votes if isinstance(item, dict))
        reports = [dict(item) for item in raw_reports if isinstance(item, dict)]
        decision = quorum_decision(self.root, graph, candidate, votes, reports)
        return timeline_report(graph, candidate, decision, reports)

    def kernel_metabolism(self, body: Mapping[str, object]) -> dict[str, object]:
        telemetry = body.get("telemetry")
        budget = body.get("budget")
        if not isinstance(telemetry, dict) or not isinstance(budget, dict):
            raise ValueError("Kernel metabolism requires telemetry and budget objects.")
        return metabolic_plan(self.kernel_graph(), telemetry, parse_metabolic_budget(budget))


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def strict_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Request JSON contains a duplicate key: {key}")
        value[key] = item
    return value


def reject_non_finite_json(value: str) -> object:
    raise ValueError(f"Request JSON contains a non-finite number: {value}")


def make_handler(runtime: ShellRuntime) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "BlunderShell/4"

        def log_message(self, format_string: str, *args: object) -> None:
            return

        def _headers(self, status: HTTPStatus, content_type: str, length: int) -> None:
            self.send_response(status.value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()

        def _json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json_bytes(value)
            self._headers(status, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def _error(self, status: HTTPStatus, message: str) -> None:
            self._json(
                {
                    "schema": "obtuse.blunder.api-error.v1",
                    "error": message,
                    "status": status.value,
                    "retryable": False,
                },
                status,
            )

        def _sse(self, page: Mapping[str, object]) -> None:
            events = page.get("events")
            if not isinstance(events, list):
                raise ValueError("Task event page does not contain an event list.")
            chunks = ["retry: 1000\n\n"]
            for item in events:
                if not isinstance(item, dict):
                    raise ValueError("Task event stream contains a malformed event.")
                sequence = item.get("sequence")
                chunks.append(
                    f"id: {sequence}\nevent: task-event\ndata: {json.dumps(item, separators=(',', ':'), ensure_ascii=False)}\n\n"
                )
            chunks.append(
                f"event: stream-position\ndata: {json.dumps({'nextCursor': page.get('nextCursor'), 'head': page.get('head')}, separators=(',', ':'))}\n\n"
            )
            body = "".join(chunks).encode("utf-8")
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            provided = self.headers.get("X-Blunder-Token", "")
            try:
                provided_bytes = provided.encode("ascii")
                expected_bytes = runtime.token.encode("ascii")
            except UnicodeEncodeError:
                return False
            return secrets.compare_digest(provided_bytes, expected_bytes)

        def _require_auth(self) -> bool:
            if self._authorized():
                return True
            self._error(HTTPStatus.UNAUTHORIZED, "Blunder Shell session token is missing or invalid.")
            return False

        def _body(self) -> dict[str, object]:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_REQUEST_BYTES:
                raise ValueError("Request body size is invalid.")
            value = json.loads(
                self.rfile.read(length).decode("utf-8"),
                object_pairs_hook=strict_object_pairs,
                parse_constant=reject_non_finite_json,
            )
            if not isinstance(value, dict):
                raise ValueError("Request body must be a JSON object.")
            return value

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/health":
                self._json({"status": "ok", "product": "Blunder Focus Shell", "version": 4})
                return
            if parsed.path.startswith("/api/") and not self._require_auth():
                return
            try:
                if parsed.path == "/api/state":
                    force = parse_qs(parsed.query).get("force", ["0"])[0] == "1"
                    self._json(runtime.get_state(force))
                    return
                if parsed.path == "/api/v1/health":
                    runtime.require_vnext()
                    self._json(
                        {
                            "schema": "obtuse.blunder.vnext-health.v1",
                            "status": "READY",
                            "product": "Blunder vNext Core",
                            "version": 1,
                            "authorityGranted": False,
                        }
                    )
                    return
                if parsed.path == "/api/v1/projects":
                    self._json(runtime.require_vnext().project_state())
                    return
                if parsed.path == "/api/v1/tasks":
                    self._json(runtime.require_vnext().list_tasks())
                    return
                if parsed.path == "/api/v1/events/stream":
                    query = parse_qs(parsed.query)
                    cursor = int(query.get("after", ["0"])[0])
                    task_id = query.get("taskId", [None])[0]
                    limit = int(query.get("limit", ["200"])[0])
                    self._sse(runtime.require_vnext().event_page(cursor, task_id, limit))
                    return
                if parsed.path == "/api/v1/recovery":
                    self._json(runtime.require_vnext().recovery_state())
                    return
                if parsed.path == "/api/v1/promotions":
                    self._json(runtime.require_vnext().promotion_state())
                    return
                if parsed.path == "/api/v1/graph":
                    query = parse_qs(parsed.query).get("q", [""])[0]
                    limit = int(parse_qs(parsed.query).get("limit", ["50"])[0])
                    self._json(query_engineering_graph(runtime.root, query, limit))
                    return
                if parsed.path.startswith("/api/v1/tasks/") and parsed.path.endswith("/artifacts"):
                    parts = parsed.path.strip("/").split("/")
                    self._json(runtime.require_vnext().task_artifacts(parts[3]))
                    return
                if parsed.path.startswith("/api/v1/tasks/"):
                    task_id = parsed.path.strip("/").split("/")[3]
                    self._json(runtime.require_vnext().get_task(task_id))
                    return
                if parsed.path == "/api/jobs":
                    self._json({"jobs": runtime.jobs.list_jobs()})
                    return
                if parsed.path == "/api/missions":
                    self._json(runtime.get_missions())
                    return
                if parsed.path == "/api/mission-branches":
                    self._json(runtime.mission_branches.state())
                    return
                if parsed.path == "/api/mission-programs":
                    self._json(runtime.mission_fabric.list())
                    return
                if parsed.path.startswith("/api/mission-programs/"):
                    program_id = parsed.path.strip("/").split("/")[2]
                    self._json(runtime.mission_fabric.get(program_id))
                    return
                if parsed.path == "/api/repository-intelligence":
                    query = parse_qs(parsed.query).get("q", [""])[0]
                    index = build_repository_index(runtime.root, True)
                    self._json(
                        intelligence_public(index)
                        | {"results": query_repository(index, query, 30) if query.strip() else []}
                    )
                    return
                if parsed.path == "/api/mission-learning":
                    self._json(outcome_summary(runtime.root, ()))
                    return
                if parsed.path.startswith("/api/mission-branches/"):
                    mission_id = parsed.path.strip("/").split("/")[2]
                    self._json(runtime.mission_branches.get(mission_id))
                    return
                if parsed.path == "/api/capabilities":
                    self._json(runtime.get_capabilities())
                    return
                if parsed.path == "/api/frontier":
                    self._json(runtime.frontier_state())
                    return
                if parsed.path == "/api/product":
                    self._json(runtime.product_state())
                    return
                if parsed.path == "/api/product/polyglot":
                    self._json(polyglot_index(runtime.root))
                    return
                if parsed.path == "/api/product/arena":
                    suite = load_product_suite(runtime.root / "configs" / "obtuse" / "blunder-product-evaluation-suite.json")
                    self._json(product_arena_report(suite, (), ()))
                    return
                if parsed.path == "/api/creation":
                    self._json(runtime.creation_state())
                    return
                if parsed.path == "/api/kernel":
                    self._json(kernel_snapshot(runtime.root))
                    return
                if parsed.path == "/api/kernel/graph":
                    self._json(runtime.kernel_graph())
                    return
                if parsed.path == "/api/ccos":
                    self._json(runtime.ccos_state())
                    return
                if parsed.path == "/api/ccos/records":
                    query = parse_qs(parsed.query)
                    kind = query.get("kind", [""])[0]
                    offset = int(query.get("offset", ["0"])[0])
                    limit = int(query.get("limit", ["100"])[0])
                    self._json(runtime.ccos_records(kind, offset, limit))
                    return
                if parsed.path == "/api/ccos/retention":
                    self._json(runtime.ccos_retention())
                    return
                if parsed.path == "/api/ccos/recovery":
                    self._json(runtime.ccos_recovery_inspection())
                    return
                if parsed.path.startswith("/api/checkpoints/"):
                    checkpoint_id = parsed.path.strip("/").split("/")[2]
                    detail = checkpoint_detail(runtime.root, checkpoint_id)
                    diffs = detail.get("diffs") if isinstance(detail.get("diffs"), list) else []
                    for item in diffs:
                        if isinstance(item, dict):
                            item["diff"] = redact_text(str(item.get("diff") or ""))
                    self._json(detail)
                    return
                if parsed.path == "/api/search":
                    query = parse_qs(parsed.query).get("q", [""])[0]
                    self._json({"results": search_source_content(runtime.root, query, 120)})
                    return
                if parsed.path.startswith("/api/jobs/"):
                    job = runtime.jobs.get_job(parsed.path.rsplit("/", 1)[-1])
                    if job is None:
                        self._error(HTTPStatus.NOT_FOUND, "Job does not exist.")
                    else:
                        self._json(job)
                    return
                if parsed.path == "/api/files":
                    query = parse_qs(parsed.query).get("q", [""])[0]
                    self._json({"results": search_files(runtime.root, query, 80)})
                    return
                if parsed.path == "/api/file":
                    relative = parse_qs(parsed.query).get("path", [""])[0]
                    self._json(read_source_file(runtime.root, relative))
                    return
                if parsed.path == "/api/editor/definitions":
                    symbol = parse_qs(parsed.query).get("symbol", [""])[0]
                    self._json(definition_locations(runtime.root, symbol))
                    return
                if parsed.path == "/api/editor/references":
                    symbol = parse_qs(parsed.query).get("symbol", [""])[0]
                    self._json(references(runtime.root, symbol, 200))
                    return
                if parsed.path == "/api/editor/completions":
                    prefix = parse_qs(parsed.query).get("prefix", [""])[0]
                    self._json(completions(runtime.root, prefix, 80))
                    return
                if parsed.path == "/api/editor/workspace-diagnostics":
                    self._json(workspace_diagnostics(runtime.root, 500))
                    return
                if parsed.path == "/api/editor/polyglot-references":
                    symbol = parse_qs(parsed.query).get("symbol", [""])[0]
                    self._json(polyglot_references(runtime.root, symbol, 500))
                    return
                self._serve_static(parsed.path)
            except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if not self._require_auth():
                return
            try:
                body = self._body()
                if parsed.path == "/api/v1/projects":
                    self._json(runtime.require_vnext().open_project(body))
                    return
                if parsed.path == "/api/v1/tasks":
                    self._json(runtime.require_vnext().create_task(body), HTTPStatus.CREATED)
                    return
                if parsed.path == "/api/v1/promotions/evaluate":
                    self._json(runtime.require_vnext().evaluate_promotion(body), HTTPStatus.CREATED)
                    return
                if parsed.path.startswith("/api/v1/tasks/") and parsed.path.endswith("/cancel"):
                    parts = parsed.path.strip("/").split("/")
                    self._json(runtime.require_vnext().cancel_task(parts[3]))
                    return
                if parsed.path.startswith("/api/v1/tasks/") and parsed.path.endswith("/launch"):
                    parts = parsed.path.strip("/").split("/")
                    self._json(runtime.require_vnext().launch_task(parts[3], body), HTTPStatus.ACCEPTED)
                    return
                if parsed.path.startswith("/api/v1/tasks/") and parsed.path.endswith("/integrate"):
                    parts = parsed.path.strip("/").split("/")
                    self._json(runtime.require_vnext().integrate_task(parts[3], body))
                    return
                if parsed.path.startswith("/api/v1/approvals/") and parsed.path.endswith("/decision"):
                    parts = parsed.path.strip("/").split("/")
                    self._json(runtime.require_vnext().decide_approval(parts[3], body))
                    return
                if parsed.path == "/api/actions":
                    action_id = str(body.get("action") or "")
                    self._json(runtime.jobs.start(action_id, body), HTTPStatus.ACCEPTED)
                    return
                if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/cancel"):
                    parts = parsed.path.strip("/").split("/")
                    self._json(runtime.jobs.cancel(parts[2]))
                    return
                if parsed.path == "/api/mission-branches":
                    self._json(
                        runtime.mission_branches.start(
                            str(body.get("task") or ""),
                            body.get("providerApproved") is True,
                        ),
                        HTTPStatus.ACCEPTED,
                    )
                    return
                if parsed.path.startswith("/api/mission-branches/") and parsed.path.endswith("/cancel"):
                    parts = parsed.path.strip("/").split("/")
                    self._json(runtime.mission_branches.cancel(parts[2]))
                    return
                if parsed.path.startswith("/api/mission-branches/") and parsed.path.endswith("/integrate"):
                    parts = parsed.path.strip("/").split("/")
                    self._json(
                        runtime.mission_branches.integrate(parts[2], str(body.get("confirmation") or ""))
                    )
                    return
                if parsed.path.startswith("/api/mission-branches/") and parsed.path.endswith("/reconcile"):
                    parts = parsed.path.strip("/").split("/")
                    self._json(
                        runtime.mission_branches.reconcile(parts[2], str(body.get("confirmation") or ""))
                    )
                    return
                if parsed.path.startswith("/api/mission-branches/") and parsed.path.endswith("/cleanup"):
                    parts = parsed.path.strip("/").split("/")
                    self._json(runtime.mission_branches.cleanup(parts[2], str(body.get("confirmation") or "")))
                    return
                if parsed.path.startswith("/api/mission-branches/") and parsed.path.endswith("/outcome"):
                    parts = parsed.path.strip("/").split("/")
                    raw_metrics = body.get("metrics")
                    metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
                    raw_score = body.get("score")
                    score = int(raw_score) if raw_score is not None else None
                    self._json(
                        runtime.mission_branches.outcome(
                            parts[2],
                            str(body.get("status") or ""),
                            score,
                            metrics,
                            str(body.get("note") or ""),
                        )
                    )
                    return
                if parsed.path == "/api/mission-programs/preview":
                    self._json(runtime.mission_fabric.preview(str(body.get("task") or "")))
                    return
                if parsed.path == "/api/mission-programs":
                    self._json(
                        runtime.mission_fabric.create(
                            str(body.get("task") or ""),
                            body.get("providerApproved") is True,
                        ),
                        HTTPStatus.ACCEPTED,
                    )
                    return
                if parsed.path.startswith("/api/mission-programs/") and parsed.path.endswith("/synthesize"):
                    parts = parsed.path.strip("/").split("/")
                    self._json(runtime.mission_fabric.synthesize(parts[2]))
                    return
                if parsed.path == "/api/briefing":
                    briefing = build_task_briefing(runtime.root, str(body.get("task") or ""))
                    context = briefing.get("context") if isinstance(briefing.get("context"), dict) else {}
                    dispatch_hooks(
                        runtime.root,
                        runtime.capabilities,
                        "task.briefed",
                        {"task": briefing.get("task"), "sourceCount": context.get("sourceCount"), "bytes": context.get("bytes")},
                    )
                    self._json(briefing)
                    return
                if parsed.path == "/api/capabilities/route":
                    self._json(runtime.route_capabilities(str(body.get("task") or "")))
                    return
                if parsed.path == "/api/capabilities/invoke":
                    self._json(
                        runtime.invoke_capability_tool(str(body.get("toolId") or "")),
                        HTTPStatus.ACCEPTED,
                    )
                    return
                if parsed.path == "/api/discovery/brief":
                    self._json(build_discovery_brief(runtime.root, str(body.get("question") or ""), 12))
                    return
                if parsed.path == "/api/self-coding/preflight":
                    self._json(runtime.self_coding_preflight(body))
                    return
                if parsed.path == "/api/product/blueprint":
                    self._json(compile_blueprint(body))
                    return
                if parsed.path == "/api/product/design-system":
                    self._json(compile_design_system(body))
                    return
                if parsed.path == "/api/product/device-matrix":
                    raw_categories = body.get("categories")
                    categories = [str(item) for item in raw_categories] if isinstance(raw_categories, list) else []
                    self._json(build_device_matrix(str(body.get("locale") or ""), categories))
                    return
                if parsed.path == "/api/product/project-audit":
                    self._json(runtime.product_project_audit(str(body.get("domain") or "")))
                    return
                if parsed.path == "/api/product/assets":
                    provenance = runtime.root / "assets.provenance.json"
                    self._json(
                        asset_intelligence_report(
                            runtime.root,
                            {"maximumAssetBytes": 5_000_000, "maximumTotalBytes": 50_000_000, "maximumImagePixels": 16_000_000},
                            provenance if provenance.is_file() else None,
                            ("first-party", "mit", "apache-2.0", "cc0"),
                        )
                    )
                    return
                if parsed.path == "/api/product/visual-judgment":
                    raw_cases = body.get("cases")
                    cases = [item for item in raw_cases if isinstance(item, dict)] if isinstance(raw_cases, list) else []
                    self._json(visual_judgment_report(runtime.root, cases))
                    return
                if parsed.path == "/api/product/arena":
                    raw_measurements = body.get("measurements")
                    raw_baselines = body.get("baselines")
                    measurements = [parse_product_measurement(item) for item in raw_measurements if isinstance(item, dict)] if isinstance(raw_measurements, list) else []
                    baselines = [item for item in raw_baselines if isinstance(item, dict)] if isinstance(raw_baselines, list) else []
                    suite = load_product_suite(runtime.root / "configs" / "obtuse" / "blunder-product-evaluation-suite.json")
                    self._json(product_arena_report(suite, measurements, baselines))
                    return
                if parsed.path == "/api/creation/creative-brief":
                    self._json(compile_creative_brief(body))
                    return
                if parsed.path == "/api/creation/ai-system":
                    self._json(compile_ai_architecture(body))
                    return
                if parsed.path == "/api/creation/automation":
                    self._json(compile_automation(body))
                    return
                if parsed.path == "/api/creation/tool":
                    self._json(compile_tool_contract(body))
                    return
                if parsed.path == "/api/creation/prompt":
                    self._json(compile_prompt_program(body))
                    return
                if parsed.path == "/api/creation/trace":
                    self._json(evaluate_trace(body))
                    return
                if parsed.path == "/api/creation/archive":
                    raw_candidates = body.get("candidates")
                    if not isinstance(raw_candidates, list) or any(not isinstance(item, dict) for item in raw_candidates):
                        raise ValueError("Creative archive candidates must be an object list.")
                    self._json(archive_creative_candidates(tuple(item for item in raw_candidates if isinstance(item, dict))))
                    return
                if parsed.path == "/api/kernel/impact":
                    self._json(runtime.kernel_impact(body))
                    return
                if parsed.path == "/api/kernel/shards":
                    self._json(runtime.kernel_shards(body))
                    return
                if parsed.path == "/api/kernel/quorum":
                    self._json(runtime.kernel_quorum(body))
                    return
                if parsed.path == "/api/kernel/timeline":
                    self._json(runtime.kernel_timeline(body))
                    return
                if parsed.path == "/api/kernel/metabolism":
                    self._json(runtime.kernel_metabolism(body))
                    return
                if parsed.path == "/api/ccos/records":
                    self._json(runtime.ccos_append(body), HTTPStatus.CREATED)
                    return
                if parsed.path == "/api/ccos/canary":
                    self._json(runtime.ccos_canary(body), HTTPStatus.CREATED)
                    return
                if parsed.path == "/api/ccos/recover":
                    self._json(runtime.ccos_recover(str(body.get("confirmation") or "")))
                    return
                if parsed.path == "/api/editor/analyze":
                    content = str(body.get("content")) if "content" in body else None
                    self._json(analyze_source_file(runtime.root, str(body.get("path") or ""), content))
                    return
                if parsed.path == "/api/editor/rename-preview":
                    self._json(rename_plan(runtime.root, str(body.get("symbol") or ""), str(body.get("replacement") or ""), 500))
                    return
                if parsed.path == "/api/editor/structural-diff":
                    relative = str(body.get("path") or "")
                    source = read_source_file(runtime.root, relative)
                    self._json(structural_diff(relative, str(source["content"]), str(body.get("content") or "")))
                    return
                if parsed.path == "/api/editor/save":
                    self._json(
                        save_source_file(
                            runtime.root,
                            str(body.get("path") or ""),
                            str(body.get("content") or ""),
                            str(body.get("expectedSha256") or ""),
                            str(body.get("confirmation") or ""),
                        )
                    )
                    runtime.invalidate_state()
                    return
                if parsed.path.startswith("/api/checkpoints/") and parsed.path.endswith("/restore"):
                    parts = parsed.path.strip("/").split("/")
                    self._json(runtime.restore(parts[2], str(body.get("confirmation") or "")))
                    return
                self._error(HTTPStatus.NOT_FOUND, "Endpoint does not exist.")
            except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))

        def _serve_static(self, request_path: str) -> None:
            relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
            candidate = (runtime.static_root / relative).resolve()
            if not candidate.is_relative_to(runtime.static_root) or not candidate.is_file():
                candidate = runtime.static_root / "index.html"
            body = candidate.read_bytes()
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
                content_type += "; charset=utf-8"
            self._headers(HTTPStatus.OK, content_type, len(body))
            self.wfile.write(body)

    return Handler


def write_session_file(root: Path, port: int, token: str) -> Path:
    artifact = shell_artifact_root(root)
    artifact.mkdir(parents=True, exist_ok=True)
    path = artifact / "server.json"
    value = {
        "schema": "obtuse.blunder.shell.server.v4",
        "pid": os.getpid(),
        "port": port,
        "host": "127.0.0.1",
        "url": f"http://127.0.0.1:{port}/?token={token}",
        "startedAt": utc_now(),
        "localhostOnly": True,
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def serve(root: Path, static_root: Path, port: int) -> None:
    token = secrets.token_urlsafe(32)
    runtime = ShellRuntime(root, static_root, token)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(runtime))
    write_session_file(root, server.server_port, token)
    print(
        json.dumps(
            {
                "status": "BLUNDER_SHELL_READY",
                "host": "127.0.0.1",
                "port": server.server_port,
                "localhostOnly": True,
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
